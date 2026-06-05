"""Rental bike catalogue for the bike-renting voice agent.

A small, self-contained catalogue (subset of the wider CyclePro product
range) so the container has no external data dependencies. Each bike
declares its rental price per day and how many units are currently
available at the shop.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Rental fleet
# ---------------------------------------------------------------------------

RENTAL_BIKES: list[dict[str, Any]] = [
    # --- City bikes ---
    {
        "id": "CB-001",
        "category": "city",
        "name": "CityRider Pro",
        "brand": "VeloUrban",
        "rental_price_per_day_eur": 18.00,
        "deposit_eur": 100.00,
        "available_units": 6,
        "frame": "Aluminium",
        "gears": 8,
        "wheel_size_inch": 28,
        "features": ["lights", "rear rack", "mudguards", "kickstand"],
        "suitable_for": "City commuting and sightseeing tours",
        "rider_height_cm": "165-185",
    },
    {
        "id": "CB-003",
        "category": "ebike",
        "name": "SpeedCommute E5",
        "brand": "ElectraRide",
        "rental_price_per_day_eur": 39.00,
        "deposit_eur": 250.00,
        "available_units": 3,
        "frame": "Aluminium",
        "gears": 5,
        "wheel_size_inch": 28,
        "features": ["250W motor", "80 km battery", "5 assist levels", "display"],
        "suitable_for": "Long city rides, hills, carrying loads",
        "rider_height_cm": "165-195",
    },
    # --- Mountain & gravel bikes ---
    {
        "id": "MB-001",
        "category": "mountain",
        "name": "TrailBlaster 29",
        "brand": "PeakRider",
        "rental_price_per_day_eur": 28.00,
        "deposit_eur": 150.00,
        "available_units": 4,
        "frame": "Aluminium",
        "gears": 21,
        "wheel_size_inch": 29,
        "features": ["100mm suspension fork", "hydraulic disc brakes", "wide knobblies"],
        "suitable_for": "Cross-country trails and forest paths",
        "rider_height_cm": "170-190",
    },
    {
        "id": "MB-002",
        "category": "mountain",
        "name": "EnduroX Full Suspension",
        "brand": "PeakRider",
        "rental_price_per_day_eur": 55.00,
        "deposit_eur": 400.00,
        "available_units": 2,
        "frame": "Carbon fibre",
        "gears": 12,
        "wheel_size_inch": 27.5,
        "features": ["150mm front / 140mm rear travel", "dropper post", "tubeless"],
        "suitable_for": "Aggressive trail and bike park days",
        "rider_height_cm": "168-190",
    },
    {
        "id": "MB-003",
        "category": "gravel",
        "name": "GravelKing 700",
        "brand": "AdventureWheels",
        "rental_price_per_day_eur": 32.00,
        "deposit_eur": 200.00,
        "available_units": 3,
        "frame": "Chromoly steel",
        "gears": 22,
        "wheel_size_inch": 700,
        "features": ["rack mounts", "45mm tyre clearance", "endurance geometry"],
        "suitable_for": "Gravel roads, light trails, day tours",
        "rider_height_cm": "165-185",
    },
    # --- Children's bikes ---
    {
        "id": "KD-002",
        "category": "children",
        "name": "JuniorTrail 20",
        "brand": "KidzRide",
        "rental_price_per_day_eur": 10.00,
        "deposit_eur": 60.00,
        "available_units": 5,
        "frame": "Aluminium",
        "gears": 6,
        "wheel_size_inch": 20,
        "features": ["adjustable saddle", "reflectors", "chain guard"],
        "suitable_for": "Kids 6-10 years on parks and bike paths",
        "rider_height_cm": "115-135",
    },
    {
        "id": "KD-003",
        "category": "children",
        "name": "TeenSport 24",
        "brand": "KidzRide",
        "rental_price_per_day_eur": 12.00,
        "deposit_eur": 80.00,
        "available_units": 4,
        "frame": "Aluminium",
        "gears": 21,
        "wheel_size_inch": 24,
        "features": ["front suspension", "disc brakes", "sporty geometry"],
        "suitable_for": "Pre-teens 10-14 on school runs and light trails",
        "rider_height_cm": "135-155",
    },
]
