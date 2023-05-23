from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
import numpy as np
from scipy.ndimage import convolve
import math

from .common import Context
from .config import ALTITUDE_ERROR, INITIAL_POSITION, POSITION_ERROR
from .utils.math import Vec2
from .utils.timer import Timer
from .navigation import Navigation
from loguru import logger

# == Simulation states == #

# Boot = 0
# SpinUp = 1
# HomeTakeOff = 2
# ToSearchZone = 3
# ScanHigh = 4
# DescendToScanLow = 5
# ScanLow = 6
# RegainAltitude = 7
# FlyToDetection = 8
# GoToPadDetection = 9
# FindBound = 10
# FlyToDestination = 11
# LandDestination = 12
# WaitAtDestination = 13
# TakeOffAgain = 14
# ReturnHome = 15
# LandHome = 16
# Stop = 17


# == Types == #


@dataclass
class Trajectory:
    altitude: float = 0.0
    orientation: float = 0.0
    position: Vec2 = field(default_factory=Vec2)


@dataclass
class FlightContext:
    ctx: Context
    navigation: Navigation

    trajectory: Trajectory = field(default_factory=Trajectory)

    home_pad: Vec2 | None = None
    over_pad: bool = False
    scan: bool = False
    target_pad: Vec2 | None = None

    # == Sensors == #

    def is_near_target(self, error=POSITION_ERROR) -> bool:
        return self.is_near_position(self.trajectory.position, error)

    def is_near_position(self, position: Vec2, error=POSITION_ERROR) -> bool:
        return (self.get_position() - position).abs() < error

    def is_near_target_altitude(self, error=ALTITUDE_ERROR) -> bool:
        return self.is_near_altitude(self.trajectory.altitude, error)

    def is_near_altitude(self, altitude: float, error=ALTITUDE_ERROR) -> bool:
        return abs(self.ctx.sensors.z - altitude) < error

    def get_position(self) -> Vec2:
        return Vec2(self.ctx.sensors.x, self.ctx.sensors.y)


class State(Protocol):
    def start(self, fctx: FlightContext) -> None:
        return

    def next(self, fctx: FlightContext) -> State | None:
        ...


# == States == #


class Boot(State):
    def next(self, fctx: FlightContext) -> State | None:
        fctx.home_pad = fctx.get_position()

        (x, y) = INITIAL_POSITION
        fctx.trajectory.position = Vec2(x, y)

        return Takeoff()


class Takeoff(State):
    def start(self, fctx: FlightContext) -> None:
        fctx.trajectory.altitude = 0.5

    def next(self, fctx: FlightContext) -> State | None:
        if fctx.is_near_target_altitude():
            return GoForward()

        return None


class Scan(State):
    def __init__(self):
        self._timer = Timer()

    def start(self, fctx: FlightContext) -> None:
        fctx.scan = True
        self._timer.reset()

    def next(self, fctx: FlightContext) -> State | None:
        if self._timer.is_elapsed(10.0):
            fctx.scan = False
            return Stop()

        return None


class GoForward(State):
    def start(self, fctx: FlightContext) -> None:
        fctx.trajectory.position.x = 2.0
        # fctx.trajectory.orientation = pi
        fctx.scan = True

    def next(self, fctx: FlightContext) -> State | None:
        if fctx.is_near_target():
            return ReturnHome()

        return None


# class GoBack(State):
#     def start(self, fctx: FlightContext) -> None:
#         fctx.trajectory.position = Vec2()

#     def next(self, fctx: FlightContext) -> State | None:
#         if fctx.is_near_target():
#             return Stop()

#         return None


class GoLower(State):
    def start(self, fctx: FlightContext):
        fctx.trajectory.altitude = 0.1
        fctx.trajectory.orientation = 0.0

    def next(self, fctx: FlightContext) -> State | None:
        if fctx.is_near_target_altitude():
            return Stop()

        return None


class Stop(State):
    def next(self, _) -> State | None:
        return None


class TargetSearch(State):
    def __init__(self):
        self.research_points = []
        self.index = 0

    def start(self, fctx: FlightContext):
        # compute target map
        self.compute_target_map(fctx)
        # set target
        fctx.trajectory.position = self.research_points[self.index]

    def next(self, fctx: FlightContext):
        if self.index == len(self.research_points):
            logger.info("No target found")
            return Stop()
        if fctx.is_near_target():
            # move to next target point
            print("no Target found on this point")
            self.index = self.index + 1
            return TargetSearch()

    def compute_target_map(self, fctx: FlightContext):
        research_points1 = [
            (4.7, 2.7),
            (4.7, 1.9),
            (4.7, 1.1),
            (4.7, 0.3),
            (4.2, 0.3),
            (4.2, 1.1),
            (4.2, 1.9),
            (4.2, 2.7),
            (3.8, 2.7),
            (3.8, 1.9),
            (3.8, 1.1),
            (3.8, 0.3),
        ]

        research_points2 = [
            (4.0, 0.8),
            (4.0, 1.5),
            (4.0, 2.3),
            (4.4, 2.3),
            (4.4, 1.5),
            (4.4, 0.8),
        ]

        research_points3 = [
            (4.7, 0.8),
            (4.7, 1.5),
            (4.7, 2.3),
            (4.4, 2.7),
            (4.4, 1.9),
            (4.4, 1.1),
            (4.4, 0.3),
            (4.2, 0.8),
            (4.2, 1.5),
            (4.2, 2.3),
            (4.0, 2.7),
            (4.0, 1.9),
            (4.0, 1.1),
            (4.0, 0.3),
            (3.8, 0.8),
            (3.8, 1.5),
            (3.8, 2.3),
        ]

        occupancy_grid = fctx.navigation.map.copy()
        kernel = np.ones((9, 9), np.uint8)
        occupancy_grid = convolve(occupancy_grid, kernel)

        i = 0
        while i < len(research_points1):
            point = fctx.navigation.to_coords(
                Vec2([research_points1[i][0], research_points1[i][1]])
            )
            point = ((np.rint(point[0])).astype(int),
                     (np.rint(point[1])).astype(int))
            if occupancy_grid[point]:
                del research_points1[i]
            else:
                i += 1

        i = 0
        while i < len(research_points2):
            point = fctx.navigation.to_coords(
                Vec2([research_points2[i][0], research_points2[i][1]]))
            point = ((np.rint(point[0])).astype(int),
                     (np.rint(point[1])).astype(int))
            if occupancy_grid[point]:
                del research_points2[i]
            else:
                i += 1

        i = 0
        while i < len(research_points3):
            point = fctx.navigation.to_coords(
                Vec2([research_points3[i][0], research_points3[i][1]]))
            point = ((np.rint(point[0])).astype(int),
                     (np.rint(point[1])).astype(int))
            if occupancy_grid[point]:
                del research_points3[i]
            else:
                i += 1

            # Move at the end isolated points
        max_dist = 1.50
        min_neighbourg = 3

        nb = 0
        id = 0
        while nb < len(research_points2):
            given_point = research_points2[id]
            count = 0
            for point in research_points2:
                if self.distance(point, given_point) < max_dist:
                    count += 1

            if count <= min_neighbourg:
                research_points2.remove(given_point)
                research_points2.append(given_point)
            else:
                id += 1
            nb += 1

        nb = 0
        id = 0
        while nb < len(research_points3):
            given_point = research_points3[id]
            count = 0
            for point in research_points3:
                if self.distance(point, given_point) < max_dist:
                    count += 1

            if count <= min_neighbourg:
                print(given_point, ' put at the end')
                research_points3.remove(given_point)
                research_points3.append(given_point)
            else:
                id += 1
            nb += 1

        research_points = research_points1.copy()
        research_points += research_points2
        research_points += research_points3

        self.research_points = research_points

    def distance(self, p1, p2):
        """
        Return the distance between two points 

        Args:
            p1 (Tuple): First point
            p2 (Tuple): Second point

        Returns:
            (Double): Distance between the points 
        """
        return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)


class TargetCentering(State):
    def __init__(self, fctx: FlightContext):
        self.target_pad = Vec2 | None
        self.x_pos : bool = False
        self.y_pos : bool = False

        self.axe_X = 0
        self.axe_Y = 1



    def start(self, fctx: FlightContext):
        pass 
        
    def next(self, fctx: FlightContext) -> State | None:
    
        
        if self.x_pos or self.y_pos:
            fctx.target_pad = self.target_pad
            return GoLower()
        
    def centering(self, data):   
            """
            Find the center of the platform.

            Args:
                sensor_data (Dictionarie): data sensors of the drone 
                
            Returns:
                commande (List): control commande of the drone
                (Boolean): Center found or not
            """
            
            lateral_movement = 0.22   
        
            self.platform_detection(data)
            
                        
            if self.change_axe >= 2: 
                if self.research_axe == self.axe_X:
                    self.research_axe = self.axe_Y
                else:
                    self.research_axe = self.axe_X
                    
                self.change_axe = 0
                self.reseatch_counter += 1
                
            stuck = False
            if self.reseatch_counter >= 5:
                stuck = True
            
            if self.platform_x_found and self.platform_y_found:
                
                commande = self.control(np.array([self.touch_down[0]-data['x_global'], self.touch_down[1]-data['y_global']]), data, 1)
                if np.linalg.norm(np.array(self.touch_down)-np.array([data['x_global'], data['y_global']])) < 0.15:
                    return commande, True, stuck
                else:
                    return commande, True, stuck # Balek d'être précis 
        
            elif self.research_axe == self.axe_X:
                if self.research_state == 0:
                    pt = self.touch_down[0] + lateral_movement
                    
                    if np.abs(data['x_global']-pt) < 0.05:
                        self.change_axe += 1
                        self.research_state = 1
                        
                else:
                    pt = self.touch_down[0] - lateral_movement
                    
                    if np.abs(data['x_global']-pt) < 0.05:
                        self.research_state = 0
                        
                commande = self.control(np.array([pt-data['x_global'], self.touch_down[1]-data['y_global']]), data, 1)
                return commande, False, stuck
            
            elif self.research_axe == self.axe_Y:
                if self.research_state == 0:
                    pt = self.touch_down[1] + lateral_movement
                
                    if np.abs(data['y_global']-pt) < 0.05:
                        self.change_axe += 1
                        self.research_state = 1
            
                else:
                    pt = self.touch_down[1] - lateral_movement
                    
                    if np.abs(data['y_global']-pt) < 0.05:
                        self.research_state = 0
                        
                commande = self.control(np.array([self.touch_down[0]-data['x_global'], pt-data['y_global']]), data, 1)       
                return commande, False, stuck          
        
    def update_platform_pos(self, position):
            """
            Update the position of the platform from the actual position
            and the direction of the movement.

            Args:
                position (List): actual position at the moment of the call
            """
            
            angle = -math.atan2(self.v_direction[1], self.v_direction[0])
            
            # Back left
            if angle >= -7*np.pi/8 and angle < -5*np.pi/8:
                print('Back left')
                if not self.platform_x_found and not self.platform_y_found:
                    self.touch_down = position
            # Left
            elif angle >= -5*np.pi/8 and angle < -3*np.pi/8:
                print('Left')
                self.touch_down = [position[0], position[1] + 0.15]
                self.platform_y_found = True
                self.change_axe = 0
                self.research_axe = self.axe_X  
            # Front left
            elif angle >= -3*np.pi/8 and angle < -np.pi/8:
                print('Front left')
                if not self.platform_x_found and not self.platform_y_found:
                    self.touch_down = position
            # Front
            elif angle >= -np.pi/8 and angle < np.pi/8:
                print('Front')
                self.touch_down = [position[0] + 0.15, position[1]]
                self.platform_x_found = True
                self.change_axe = 0
                self.research_axe = self.axe_Y
            # Front right
            elif angle >= np.pi/8 and angle < 3*np.pi/8:
                print('Front right')
                if not self.platform_x_found and not self.platform_y_found:
                    self.touch_down = position
            # Right
            elif angle >= 3*np.pi/8 and angle < 5*np.pi/8:
                print('Right')
                self.touch_down = [position[0], position[1] - 0.15]
                self.platform_y_found = True
                self.change_axe = 0
                self.research_axe = self.axe_X
            # Back right
            elif angle >= 5*np.pi/8 and angle < 7*np.pi/8:
                print('Back right')
                if not self.platform_x_found and not self.platform_y_found:
                    self.touch_down = position
            # Back
            elif angle >= 7*np.pi/8 or angle < -7*np.pi/8:
                print('Back')
                self.touch_down = [position[0] - 0.15, position[1]]
                self.platform_x_found = True
                self.change_axe = 0
                self.research_axe = self.axe_Y
                

class ReturnHome(State):
    def start(self, fctx: FlightContext):
        assert fctx.home_pad is not None

        # kalman is reset when the motors stop at the top pad RAJOUTER UNE FONCTION
        fctx.trajectory.position = fctx.home_pad

    def next(self, fctx: FlightContext) -> State | None:
        if fctx.is_near_target():
            return GoLower()

        return None
