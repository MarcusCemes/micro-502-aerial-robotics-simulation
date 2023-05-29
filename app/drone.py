from __future__ import annotations

import threading
from asyncio import Event, Future, get_event_loop, get_running_loop, sleep
from dataclasses import replace
from enum import Enum
from typing import Any
import numpy as np
import matplotlib.pyplot as plt

from loguru import logger

from cflib.crazyflie import Crazyflie
from cflib.crazyflie.log import LogConfig

from .config import (
    CACHE_DIR,
    INITIAL_POSITION,
    RANGE_LOG_PERIOD_MS,
    STAB_LOG_PERIOD_MS,
    URI,
    MAP_PX_PER_M,
)
from .types import Sensors
from .utils.math import Vec2, deg_to_rad, mm_to_m


class LogNames(Enum):
    Stabiliser = "stab"
    Range = "range"


STAB_SENSORS = [
    ("stateEstimate.x", "float"),
    ("stateEstimate.y", "float"),
    ("stateEstimate.z", "float"),
    ("stabilizer.yaw", "float"),
    ("range.front", "uint16_t"),
    ("range.back", "uint16_t"),
    ("range.left", "uint16_t"),
    ("range.right", "uint16_t"),
]

RANGE_SENSORS = [
    ("range.zrange", "uint16_t"),
    ("stabilizer.roll", "float"),
    ("stabilizer.pitch", "float"),
    ("stateEstimate.vx", "float"),
    ("stateEstimate.vy", "float"),
]


class ProbalityMap:
    def __init__(self) -> None:
        self.map_offset = 3.5
        self.size = (int(float(MAP_PX_PER_M) * 1.5), int(float(MAP_PX_PER_M) * 3.0))
        self.probability_map = np.zeros(self.size)

    def fill(self, fctx: FlightContext):
        coords = self.to_coords(fctx.navigation.global_position())
        self.probability_map[coords[0], coords[1]] = max(
            np.sum(np.abs(np.diff(fctx.ctx.drone.down_hist))),
            self.probability_map[coords[0], coords[1]],
        )

    def to_coords(self, position: Vec2):
        px_x, px_y = self.size
        size_x, size_y = 1.5, 3.0

        cx = int((position.x - self.map_offset) * px_x / size_x)
        cy = int(position.y * px_y / size_y)

        return (cx, cy)

    def to_position(self, coords):
        (y, x) = coords

        return Vec2(
            ((x + 0.5) / MAP_PX_PER_M) + self.map_offset, (y + 0.5) / MAP_PX_PER_M
        )

    def process_map(self):
        print(np.amax(self.probability_map))
        temp = (self.probability_map > 0) * self.probability_map
        threshold = np.median(temp)
        map = (self.probability_map > threshold + 29) * self.probability_map
        plt.imsave("sssssy_map.png", map)

        return map

    def find_mean_position(self):
        map = self.process_map()

        x_coords, y_coords = np.meshgrid(
            range(map.shape[1]),
            range(map.shape[0]),
        )

        mean_x = np.sum(x_coords * map) / np.sum(map)
        mean_y = np.sum(y_coords * map) / np.sum(map)

        return self.to_position((mean_x, mean_y))

    def save(self):
        plt.imsave("probability_map.png", self.probability_map)


class Drone:
    def __init__(self, data_event: Event) -> None:
        self.cf = Crazyflie(rw_cache=CACHE_DIR)

        self._connection_future: Future[Any] | None = None
        self._data_event = data_event
        self._last_sensor_data: Sensors = Sensors()
        self._lock = threading.Lock()
        self._loop = get_running_loop()

        self._sensors = Sensors()

        self.prob_map = ProbalityMap()

        self.fast_speed = False
        self.first_landing = False
        self.last_z: float = 0
        self.down_hist = np.zeros(8)
        self.tot_down_hist = []
        self.tot_mean = []
        self.tot_diff = []

    async def __aenter__(self) -> Drone:
        await self.connect()
        return self

    async def __aexit__(self, *_) -> None:
        self.disconnect()

    async def connect(self) -> None:
        assert self._connection_future is None

        logger.info("📶 Connecting...")

        self._connection_future = get_event_loop().create_future()

        self.cf.connected.add_callback(self._on_connect)
        self.cf.disconnected.add_callback(self._on_disconnect)
        self.cf.connection_failed.add_callback(self._on_connection_failed)
        self.cf.connection_lost.add_callback(self._on_connection_lost)

        self.cf.open_link(URI)

        try:
            await self._connection_future
        finally:
            self._connection_future = None

    def configure_logging(self) -> None:
        assert self.cf.is_connected()

        for name, sensors, period in [
            (LogNames.Stabiliser, STAB_SENSORS, STAB_LOG_PERIOD_MS),
            (LogNames.Range, RANGE_SENSORS, RANGE_LOG_PERIOD_MS),
        ]:
            cfg = self._generate_logging_config(name, sensors, period)

            self.cf.log.add_config(cfg)

            cfg.data_received_cb.add_callback(self._on_sensor_data)
            cfg.error_cb.add_callback(self._on_sensor_error)

            cfg.start()

    def disconnect(self) -> None:
        if self.cf.is_connected():
            self.cf.close_link()
            logger.info("📶 Link closed")

    def get_last_sensor_reading(self) -> Sensors:
        with self._lock:
            return self._last_sensor_data

    async def reset_estimator(self, pos: Vec2 = Vec2(*INITIAL_POSITION)) -> None:
        logger.debug("🗿 Resetting Kalman estimator...")

        self.cf.param.set_value("kalman.initialX", f"{pos.x:.2f}")
        self.cf.param.set_value("kalman.initialY", f"{pos.y:.2f}")
        self.cf.param.set_value("kalman.resetEstimation", "1")
        await sleep(0.1)
        self.cf.param.set_value("kalman.resetEstimation", "0")

    # == Private == #

    def _generate_logging_config(
        self, name: LogNames, sensors: list[tuple[str, str]], period
    ) -> LogConfig:
        cfg = LogConfig(name=name, period_in_ms=period)

        for var, type in sensors:
            cfg.add_variable(var, type)

        return cfg

    # == Callbacks == #

    def _on_connect(self, _) -> None:
        logger.debug("Connected")

        if self._connection_future is not None:
            self._connection_future.get_loop().call_soon_threadsafe(
                self._connection_future.set_result, None
            )

    def _on_disconnect(self, _) -> None:
        pass

    def _on_connection_failed(self, _, e: str) -> None:
        logger.warning(f"Connection failed")

        if self._connection_future is not None:
            self._connection_future.get_loop().call_soon_threadsafe(
                self._connection_future.set_exception, ConnectionError(e)
            )

    def _on_connection_lost(self, _, msg: str) -> None:
        logger.warning(f"Connection lost: {msg}")

    def _on_sensor_data(self, _, data, cfg: LogConfig) -> None:
        match cfg.name:
            case LogNames.Stabiliser:
                self._sensors.x = data["stateEstimate.x"]
                self._sensors.y = data["stateEstimate.y"]
                self._sensors.z = data["stateEstimate.z"]
                self._sensors.back = float(mm_to_m(data["range.back"]))
                self._sensors.front = float(mm_to_m(data["range.front"]))
                self._sensors.left = float(mm_to_m(data["range.left"]))
                self._sensors.right = float(mm_to_m(data["range.right"]))
                self._sensors.yaw = deg_to_rad(data["stabilizer.yaw"])

            case LogNames.Range:
                self._sensors.down = float(mm_to_m(data["range.zrange"]))
                self._sensors.roll = float(deg_to_rad(data["stabilizer.roll"]))
                self._sensors.pitch = float(deg_to_rad(data["stabilizer.pitch"]))
                self.down_hist = np.append(
                    self.down_hist[1:],
                    np.cos(self._sensors.roll)
                    * np.cos(self._sensors.pitch)
                    * data["range.zrange"],
                )
                self.tot_down_hist.append(data["range.zrange"])
                self.tot_mean.append(np.mean(self.down_hist))
                self.tot_diff.append(np.sum(np.abs(np.diff(self.down_hist))))
                self._sensors.vx = float(data["stateEstimate.vx"])
                self._sensors.vy = float(data["stateEstimate.vy"])

        with self._lock:
            self._last_sensor_data = replace(self._sensors)

        self._loop.call_soon_threadsafe(self._data_event.set)

    def _on_sensor_error(self, cfg: LogConfig, msg: str):
        logger.warning(f"Sensor logging error ({cfg.name}): {msg}")
