"""Bike sample data for product guide, support hotline, and repair status agents."""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Bike catalogue
# ---------------------------------------------------------------------------

BIKES: list[dict[str, Any]] = [
    # --- City Bikes ---
    {
        "id": "CB-001",
        "category": "city",
        "name": "CityRider Pro",
        "brand": "VeloUrban",
        "price_eur": 799.00,
        "frame": "Aluminium",
        "gears": 8,
        "brakes": "Hydraulic disc",
        "weight_kg": 13.5,
        "wheel_size_inch": 28,
        "color": ["matte black", "silver", "teal"],
        "features": [
            "Integrated rear rack",
            "Built-in front and rear lights",
            "Mudguards included",
            "Kickstand",
            "Ergonomic saddle",
        ],
        "description": (
            "The CityRider Pro is the ideal commuter bike. Its lightweight aluminium frame, "
            "8-speed Shimano drivetrain, and hydraulic disc brakes make city riding comfortable "
            "and safe in all weather conditions. Integrated lighting means you are always visible."
        ),
        "suitable_for": "Daily commuting, city errands, light touring",
        "recommended_rider_height_cm": "165-185",
    },
    {
        "id": "CB-002",
        "category": "city",
        "name": "UrbanGlide Comfort",
        "brand": "VeloUrban",
        "price_eur": 549.00,
        "frame": "Steel",
        "gears": 7,
        "brakes": "V-brake",
        "weight_kg": 15.2,
        "wheel_size_inch": 28,
        "color": ["white", "pastel blue", "rose gold"],
        "features": [
            "Step-through frame",
            "Basket mount",
            "Comfortable upright riding position",
            "Puncture-resistant tyres",
        ],
        "description": (
            "The UrbanGlide Comfort features a classic step-through frame for easy mounting. "
            "It's built for relaxed riding through city streets and parks, with puncture-resistant "
            "tyres for worry-free commuting."
        ),
        "suitable_for": "Leisurely city rides, shopping, short commutes",
        "recommended_rider_height_cm": "155-180",
    },
    {
        "id": "CB-003",
        "category": "city",
        "name": "SpeedCommute E5",
        "brand": "ElectraRide",
        "price_eur": 1899.00,
        "frame": "Aluminium",
        "gears": 5,
        "brakes": "Hydraulic disc",
        "weight_kg": 22.0,
        "wheel_size_inch": 28,
        "color": ["graphite", "navy"],
        "features": [
            "250W rear hub motor",
            "Battery range 80 km",
            "Integrated display",
            "USB charging port",
            "5 assist levels",
        ],
        "description": (
            "The SpeedCommute E5 is our flagship e-bike for city commuters. With a 250W motor "
            "and 80 km battery range, you can tackle hills and long distances effortlessly. "
            "The integrated display shows speed, battery level, and assist mode."
        ),
        "suitable_for": "Long commutes, hilly cities, carrying loads",
        "recommended_rider_height_cm": "165-195",
    },
    # --- Mountain Bikes ---
    {
        "id": "MB-001",
        "category": "mountain",
        "name": "TrailBlaster 29",
        "brand": "PeakRider",
        "price_eur": 1299.00,
        "frame": "Aluminium",
        "gears": 21,
        "brakes": "Hydraulic disc",
        "weight_kg": 14.8,
        "wheel_size_inch": 29,
        "color": ["neon green", "black"],
        "features": [
            "100mm front suspension fork",
            "Shimano Deore drivetrain",
            "Tubeless-ready rims",
            "Dropper post compatible",
            "Wide aggressive tread tyres",
        ],
        "description": (
            "The TrailBlaster 29 is built for trail enthusiasts who demand performance. "
            "Its 29-inch wheels roll over obstacles with ease, while the 100mm suspension fork "
            "absorbs trail chatter. The Shimano Deore 21-speed drivetrain handles every climb."
        ),
        "suitable_for": "Cross-country trails, forest paths, moderate descents",
        "recommended_rider_height_cm": "170-190",
    },
    {
        "id": "MB-002",
        "category": "mountain",
        "name": "EnduroX Full Suspension",
        "brand": "PeakRider",
        "price_eur": 2499.00,
        "frame": "Carbon fibre",
        "gears": 12,
        "brakes": "4-piston hydraulic disc",
        "weight_kg": 13.2,
        "wheel_size_inch": 27.5,
        "color": ["volcanic orange", "stealth grey"],
        "features": [
            "150mm front + 140mm rear suspension",
            "SRAM Eagle 12-speed drivetrain",
            "Tubeless tyres included",
            "Dropper seatpost",
            "Internal cable routing",
        ],
        "description": (
            "The EnduroX Full Suspension is the ultimate machine for aggressive trail and enduro "
            "riding. The carbon frame keeps weight low while 150/140mm of travel tackles big hits. "
            "SRAM Eagle 12-speed ensures you never run out of gears on the steepest climbs."
        ),
        "suitable_for": "Enduro, aggressive trail riding, bike park laps",
        "recommended_rider_height_cm": "168-190",
    },
    {
        "id": "MB-003",
        "category": "mountain",
        "name": "GravelKing 700",
        "brand": "AdventureWheels",
        "price_eur": 999.00,
        "frame": "Chromoly steel",
        "gears": 22,
        "brakes": "Mechanical disc",
        "weight_kg": 11.5,
        "wheel_size_inch": 700,
        "color": ["sand", "olive green"],
        "features": [
            "Gravel/adventure geometry",
            "Rack and mudguard mounts",
            "Tyre clearance up to 45mm",
            "Relaxed endurance position",
        ],
        "description": (
            "The GravelKing 700 bridges city and trail. Its chromoly steel frame is tough and "
            "compliant, soaking up road buzz on long rides. Wide tyre clearance lets you run "
            "slicks in the city or knobblies off-road."
        ),
        "suitable_for": "Gravel roads, light trails, cycle touring, commuting",
        "recommended_rider_height_cm": "165-185",
    },
    # --- Children's Bikes ---
    {
        "id": "KD-001",
        "category": "children",
        "name": "PedalPal 16",
        "brand": "KidzRide",
        "price_eur": 199.00,
        "frame": "Steel",
        "gears": 1,
        "brakes": "Coaster + hand brake",
        "weight_kg": 7.8,
        "wheel_size_inch": 16,
        "color": ["red", "blue", "pink"],
        "features": [
            "Training-wheel compatible",
            "Chain guard",
            "Safety handlebar pad",
            "Soft ergonomic grips",
        ],
        "description": (
            "The PedalPal 16 is the perfect first pedal bike for children aged 4-6. "
            "The lightweight steel frame keeps it manageable, and the dual braking system "
            "teaches children proper braking technique while remaining safe."
        ),
        "suitable_for": "First bike, backyard, bike paths",
        "recommended_rider_height_cm": "105-120",
        "age_range": "4-6 years",
    },
    {
        "id": "KD-002",
        "category": "children",
        "name": "JuniorTrail 20",
        "brand": "KidzRide",
        "price_eur": 289.00,
        "frame": "Aluminium",
        "gears": 6,
        "brakes": "V-brake",
        "weight_kg": 9.1,
        "wheel_size_inch": 20,
        "color": ["yellow", "purple", "black"],
        "features": [
            "6-speed Shimano gears",
            "Adjustable saddle and handlebar",
            "Rear derailleur protector",
            "Reflectors all round",
        ],
        "description": (
            "The JuniorTrail 20 grows with your child. The wide saddle and handlebar height "
            "range accommodates riders from 115 to 135 cm. Six-speed gears introduce children "
            "to shifting while keeping the drivetrain simple and reliable."
        ),
        "suitable_for": "School commuting, parks, light off-road",
        "recommended_rider_height_cm": "115-135",
        "age_range": "6-10 years",
    },
    {
        "id": "KD-003",
        "category": "children",
        "name": "TeenSport 24",
        "brand": "KidzRide",
        "price_eur": 399.00,
        "frame": "Aluminium",
        "gears": 21,
        "brakes": "Mechanical disc",
        "weight_kg": 11.0,
        "wheel_size_inch": 24,
        "color": ["stealth black", "electric blue"],
        "features": [
            "Front suspension fork",
            "21-speed Shimano",
            "Disc brakes for reliable stopping",
            "Sporty geometry",
        ],
        "description": (
            "The TeenSport 24 is designed for active pre-teens who want a real bike. "
            "With front suspension, 21 speeds, and disc brakes, it handles school runs "
            "and weekend trail rides with equal confidence."
        ),
        "suitable_for": "School, trails, parks",
        "recommended_rider_height_cm": "135-155",
        "age_range": "10-14 years",
    },
]

# ---------------------------------------------------------------------------
# Common support questions per category
# ---------------------------------------------------------------------------

SUPPORT_QUESTIONS: dict[str, list[str]] = {
    "city": [
        "My electric bike battery is not charging — what should I check?",
        "How do I adjust the hydraulic disc brakes on my city bike?",
        "The gear shifting is skipping on my 8-speed — how do I fix it?",
        "My bike lights stopped working even though the battery is charged.",
        "How often should I service the hydraulic brakes?",
        "The rear rack is rattling — how do I tighten it?",
        "What tyre pressure is recommended for city riding?",
        "How do I remove and refit the rear wheel for a puncture repair?",
    ],
    "mountain": [
        "My suspension fork feels too stiff — how do I adjust the air pressure?",
        "The dropper seatpost is not dropping when I press the lever.",
        "How do I bleed the 4-piston hydraulic brakes?",
        "My SRAM Eagle derailleur is skipping under load — what needs adjusting?",
        "The front suspension is leaking oil — what should I do?",
        "How do I set up tubeless tyres on my trail bike?",
        "The creaking noise from the bottom bracket — how do I diagnose it?",
        "My rear shock feels too soft — how do I adjust the rebound damping?",
    ],
    "children": [
        "The training wheels on my child's bike are uneven — how do I level them?",
        "How do I adjust the saddle height as my child grows?",
        "The coaster brake on the PedalPal 16 is not engaging properly.",
        "My child's chain keeps falling off — how do I fix the chain tension?",
        "How do I replace the handlebar grips on a kids' bike?",
        "The V-brakes are squealing — how do I adjust the pads?",
        "What is the maximum rider weight for the JuniorTrail 20?",
        "How do I remove the training wheels when my child is ready?",
    ],
}

# ---------------------------------------------------------------------------
# Repair job database (in-memory)
# ---------------------------------------------------------------------------

REPAIR_JOBS: dict[str, dict[str, Any]] = {
    "REP-1001": {
        "job_id": "REP-1001",
        "customer_name": "Alice Müller",
        "bike_model": "CityRider Pro",
        "bike_id": "CB-001",
        "issue": "Hydraulic brake bleed — front and rear",
        "status": "completed",
        "scheduled_date": "2026-05-20",
        "completion_date": "2026-05-20",
        "mechanic": "Jonas Weber",
        "estimated_cost_eur": 85.00,
        "actual_cost_eur": 85.00,
        "notes": "Both brake calipers bled and pads replaced. Bike test-ridden.",
    },
    "REP-1002": {
        "job_id": "REP-1002",
        "customer_name": "Ben Schreiber",
        "bike_model": "TrailBlaster 29",
        "bike_id": "MB-001",
        "issue": "Suspension fork service — 100-hour interval",
        "status": "in_progress",
        "scheduled_date": "2026-05-29",
        "completion_date": None,
        "mechanic": "Maria Hoffman",
        "estimated_cost_eur": 120.00,
        "actual_cost_eur": None,
        "notes": "Fork disassembled, new bath oil and foam rings installed. Awaiting seal kit.",
    },
    "REP-1003": {
        "job_id": "REP-1003",
        "customer_name": "Clara Fischer",
        "bike_model": "SpeedCommute E5",
        "bike_id": "CB-003",
        "issue": "Battery not holding charge — diagnostics and replacement",
        "status": "waiting_for_parts",
        "scheduled_date": "2026-05-28",
        "completion_date": None,
        "mechanic": "Jonas Weber",
        "estimated_cost_eur": 350.00,
        "actual_cost_eur": None,
        "notes": "Battery diagnosed as faulty cell. Replacement battery ordered from ElectraRide.",
    },
    "REP-1004": {
        "job_id": "REP-1004",
        "customer_name": "David König",
        "bike_model": "EnduroX Full Suspension",
        "bike_id": "MB-002",
        "issue": "Full service — drivetrain clean, cables, tubeless re-seat",
        "status": "scheduled",
        "scheduled_date": "2026-06-02",
        "completion_date": None,
        "mechanic": "Maria Hoffman",
        "estimated_cost_eur": 180.00,
        "actual_cost_eur": None,
        "notes": "",
    },
    "REP-1005": {
        "job_id": "REP-1005",
        "customer_name": "Emma Bauer",
        "bike_model": "JuniorTrail 20",
        "bike_id": "KD-002",
        "issue": "Gear cable replacement and derailleur adjustment",
        "status": "completed",
        "scheduled_date": "2026-05-22",
        "completion_date": "2026-05-22",
        "mechanic": "Jonas Weber",
        "estimated_cost_eur": 40.00,
        "actual_cost_eur": 40.00,
        "notes": "New gear cables fitted, derailleur indexed. Shifting smooth across all 6 gears.",
    },
    "REP-1006": {
        "job_id": "REP-1006",
        "customer_name": "Frank Neumann",
        "bike_model": "GravelKing 700",
        "bike_id": "MB-003",
        "issue": "Tubeless tyre puncture repair — rear wheel",
        "status": "scheduled",
        "scheduled_date": "2026-06-05",
        "completion_date": None,
        "mechanic": "Maria Hoffman",
        "estimated_cost_eur": 35.00,
        "actual_cost_eur": None,
        "notes": "",
    },
}

# Next job ID counter (for new bookings)
_next_job_number = 1007


def get_next_job_id() -> str:
    global _next_job_number
    job_id = f"REP-{_next_job_number}"
    _next_job_number += 1
    return job_id
