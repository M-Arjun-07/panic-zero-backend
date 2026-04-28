"""
PanicZero — A* Pathfinding Engine
===================================
Computes the optimal evacuation route through a building graph,
avoiding hazard zones, preferring stairs over lifts in emergencies.

The node graph mirrors the exact room layout in DigitalTwin.jsx
so coordinates returned can be rendered directly by the frontend.

Spec: "Provide evacuation route as list of coordinates for Person 2 to render"
"""

import heapq
import math
from typing import Optional

# ─────────────────────────────────────────────
#  Building Graph
#  Matches roomCoordinates in DigitalTwin.jsx exactly
#  Format: { room_id: [x, y, z] }
# ─────────────────────────────────────────────

FLOOR_H = 3.5  # Must match frontend floorH

def _build_room_coordinates() -> dict:
    coords = {}

    # Vertical cores (floors 0-4)
    for level in range(5):
        y = level * FLOOR_H
        coords[f"Lift_F{level}"]  = [0,   y, 0]
        coords[f"StairA_F{level}"] = [-14, y, 0]
        coords[f"StairB_F{level}"] = [14,  y, 0]

    # Ground floor
    coords["Lobby"]      = [0,  0,  4]
    coords["Restaurant"] = [8,  0,  4]
    coords["Kitchen"]    = [8,  0, -4]
    coords["Security"]   = [-8, 0,  4]
    coords["Utility"]    = [-8, 0, -4]

    # Upper floors 1-4
    for floor in range(1, 5):
        y = floor * FLOOR_H
        coords[f"Corridor_{floor}"] = [0, y, 0]
        coords[f"{floor}01"] = [-9, y, -4]
        coords[f"{floor}02"] = [-3, y, -4]
        coords[f"{floor}03"] = [3,  y, -4]
        coords[f"{floor}04"] = [9,  y, -4]
        coords[f"{floor}05"] = [-9, y,  4]
        coords[f"{floor}06"] = [-3, y,  4]
        coords[f"{floor}07"] = [3,  y,  4]
        coords[f"{floor}08"] = [9,  y,  4]

    # Exit
    coords["Main_Exit"] = [0, 0, 8]
    return coords

ROOM_COORDINATES = _build_room_coordinates()


# ─────────────────────────────────────────────
#  Adjacency Graph
#  Defines which rooms connect to which.
#  Edge weight = Euclidean distance (auto-calculated).
# ─────────────────────────────────────────────

def _dist(a: str, b: str) -> float:
    ca, cb = ROOM_COORDINATES.get(a), ROOM_COORDINATES.get(b)
    if not ca or not cb:
        return float("inf")
    return math.sqrt(sum((ca[i] - cb[i]) ** 2 for i in range(3)))


def _build_graph() -> dict:
    """
    Returns adjacency dict: { room: [(neighbour, weight), ...] }
    Models real building connectivity — rooms connect to corridor,
    corridor connects to stairs/lifts, stairs connect floor-to-floor.
    """
    graph: dict[str, list[tuple[str, float]]] = {room: [] for room in ROOM_COORDINATES}

    def connect(a: str, b: str):
        if a in graph and b in graph:
            w = _dist(a, b)
            graph[a].append((b, w))
            graph[b].append((a, w))

    # Ground floor internal connections
    connect("Lobby",      "Restaurant")
    connect("Lobby",      "Security")
    connect("Lobby",      "Kitchen")
    connect("Lobby",      "Utility")
    connect("Lobby",      "Lift_F0")
    connect("Lobby",      "StairA_F0")
    connect("Lobby",      "StairB_F0")
    connect("Lobby",      "Main_Exit")
    connect("Restaurant", "Kitchen")
    connect("Security",   "Utility")
    connect("Restaurant", "StairB_F0")
    connect("Security",   "StairA_F0")

    # Upper floors: rooms → corridor → vertical cores
    for floor in range(1, 5):
        corridor = f"Corridor_{floor}"
        lift     = f"Lift_F{floor}"
        stairA   = f"StairA_F{floor}"
        stairB   = f"StairB_F{floor}"

        # Corridor connects to all rooms on this floor
        for suffix in ["01", "02", "03", "04", "05", "06", "07", "08"]:
            connect(corridor, f"{floor}{suffix}")

        # Corridor connects to vertical cores
        connect(corridor, lift)
        connect(corridor, stairA)
        connect(corridor, stairB)

    # Vertical cores: floor-to-floor connections
    for floor in range(4):
        # Stairs connect consecutive floors (both A and B)
        connect(f"StairA_F{floor}", f"StairA_F{floor+1}")
        connect(f"StairB_F{floor}", f"StairB_F{floor+1}")
        # Lift connects all floors (but can be disabled if stuck/fire)
        connect(f"Lift_F{floor}", f"Lift_F{floor+1}")

    # Ground floor stairs connect to lobby
    connect("StairA_F0", "Lobby")
    connect("StairB_F0", "Lobby")
    connect("Lift_F0",   "Lobby")

    return graph

BUILDING_GRAPH = _build_graph()


# ─────────────────────────────────────────────
#  A* Pathfinding
# ─────────────────────────────────────────────

def _heuristic(a: str, b: str) -> float:
    """Euclidean distance heuristic between two rooms."""
    return _dist(a, b)


def astar(
    start: str,
    goal: str,
    blocked_rooms: list[str],
    allow_lift: bool = True,
) -> list[str]:
    """
    A* search on the building graph.

    Args:
        start:         Starting room node ID
        goal:          Target room node ID (typically 'Main_Exit')
        blocked_rooms: List of room IDs to treat as impassable (hazard zones)
        allow_lift:    If False, lift nodes are penalised (fire/violence scenarios)

    Returns:
        Ordered list of room IDs from start → goal, or [] if no path found.
    """
    blocked = set(blocked_rooms)

    # Priority queue: (f_score, g_score, node, path)
    open_heap: list[tuple[float, float, str, list]] = []
    heapq.heappush(open_heap, (0.0, 0.0, start, [start]))

    visited: dict[str, float] = {}  # node → best g_score seen

    while open_heap:
        f, g, current, path = heapq.heappop(open_heap)

        if current == goal:
            return path

        if current in visited and visited[current] <= g:
            continue
        visited[current] = g

        for neighbour, weight in BUILDING_GRAPH.get(current, []):
            if neighbour in blocked:
                continue

            # Heavy penalty for lift use in dangerous situations
            extra = 0.0
            if not allow_lift and "Lift" in neighbour:
                extra = 1000.0

            new_g = g + weight + extra
            h = _heuristic(neighbour, goal)
            new_f = new_g + h
            heapq.heappush(open_heap, (new_f, new_g, neighbour, path + [neighbour]))

    return []  # No path found


# ─────────────────────────────────────────────
#  High-Level Evacuation Route Builder
# ─────────────────────────────────────────────

# Crises where lifts should NOT be used
LIFT_UNSAFE_TYPES = {"fire", "gas", "FIRE", "GAS", "violence", "VIOLENCE"}


def get_evacuation_route(
    start_room: str,
    hazard_rooms: list[str],
    crisis_type: str = "GENERAL",
    goal: str = "Main_Exit",
) -> dict:
    """
    Computes the safest evacuation route using A*.

    Args:
        start_room:   Room where the person/incident is located
        hazard_rooms: List of rooms to avoid (on fire, flooded, etc.)
        crisis_type:  Crisis type string — affects lift safety decision
        goal:         Destination (default: Main_Exit)

    Returns:
        Dict with:
          - route_nodes:   ordered list of room name strings
          - coordinates:   ordered list of [x, y, z] for each node
          - distance:      total path length
          - lift_used:     bool
          - blocked_zones: the rooms that were avoided
          - found:         bool — False if no safe path exists
    """
    # Validate start room
    if start_room not in ROOM_COORDINATES:
        # Fuzzy fallback: try matching by substring
        matches = [r for r in ROOM_COORDINATES if start_room.lower() in r.lower()]
        start_room = matches[0] if matches else "Lobby"

    allow_lift = crisis_type not in LIFT_UNSAFE_TYPES

    print(f"\n[PATHFINDING]   A* Route: '{start_room}' → '{goal}'")
    print(f"   Crisis type : {crisis_type}")
    print(f"   Lift allowed: {allow_lift}")
    print(f"   Blocked zones: {hazard_rooms}")

    route = astar(
        start=start_room,
        goal=goal,
        blocked_rooms=hazard_rooms,
        allow_lift=allow_lift,
    )

    if not route:
        # Last resort: try ignoring hazard rooms (better than nothing)
        print("    No safe route found. Attempting route through hazard zones...")
        route = astar(start=start_room, goal=goal, blocked_rooms=[], allow_lift=allow_lift)

    if not route:
        print("   No path found at all.")
        return {
            "found": False,
            "route_nodes": [],
            "coordinates": [],
            "distance": 0,
            "lift_used": False,
            "blocked_zones": hazard_rooms,
        }

    # Build coordinate list for frontend rendering
    coordinates = [ROOM_COORDINATES[node] for node in route]

    # Calculate total path distance
    total_dist = sum(
        _dist(route[i], route[i + 1]) for i in range(len(route) - 1)
    )

    lift_used = any("Lift" in node for node in route)

    print(f"   Route found: {' → '.join(route)}")
    print(f"   Distance: {total_dist:.1f} units | Lift used: {lift_used}")

    return {
        "found": True,
        "route_nodes": route,
        "coordinates": coordinates,
        "distance": round(total_dist, 2),
        "lift_used": lift_used,
        "blocked_zones": hazard_rooms,
        "node_count": len(route),
    }
