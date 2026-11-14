"""
Database Seed Script — Populate sample rooms
Usage: python -m utils.seed_rooms

Creates 20 rooms of each type (Standard, Deluxe, Suite, Executive, Presidential)
Total: 100 rooms
"""

import asyncio
import logging
from database.mongodb import connect_db, disconnect_db, get_db
from database.models import Room, RoomType, BedType, RoomPricing, RoomAmenities

logger = logging.getLogger(__name__)


# Room type configurations (20 rooms each)
ROOM_TYPE_CONFIGS = {
    RoomType.STANDARD: {
        "floor_start": 1,
        "room_start": 101,
        "capacity": 2,
        "size_sqft": 250,
        "description": "Comfortable standard room with modern amenities",
        "bed_type": BedType.DOUBLE,
        "amenities": RoomAmenities(
            wifi=True, ac=True, tv=True, workspace=True
        ),
        "pricing": RoomPricing(
            base_price_per_night=4500,
            tax_pct=15
        ),
    },
    RoomType.DELUXE: {
        "floor_start": 3,
        "room_start": 301,
        "capacity": 2,
        "size_sqft": 350,
        "description": "Luxurious deluxe room with premium amenities",
        "bed_type": BedType.QUEEN,
        "amenities": RoomAmenities(
            wifi=True, ac=True, tv=True, minibar=True, 
            balcony=True, workspace=True, breakfast_included=True
        ),
        "pricing": RoomPricing(
            base_price_per_night=8500,
            weekend_surcharge_pct=10,
            discount_weekly_pct=10,
            tax_pct=15
        ),
    },
    RoomType.SUITE: {
        "floor_start": 5,
        "room_start": 501,
        "capacity": 3,
        "size_sqft": 500,
        "description": "Spacious suite with separate living area",
        "bed_type": BedType.KING,
        "amenities": RoomAmenities(
            wifi=True, ac=True, tv=True, minibar=True, 
            balcony=True, kitchen=True, workspace=True, 
            breakfast_included=True
        ),
        "pricing": RoomPricing(
            base_price_per_night=15000,
            weekend_surcharge_pct=15,
            discount_weekly_pct=15,
            tax_pct=15
        ),
    },
    RoomType.EXECUTIVE: {
        "floor_start": 7,
        "room_start": 701,
        "capacity": 2,
        "size_sqft": 450,
        "description": "Executive room with premium business amenities",
        "bed_type": BedType.KING,
        "amenities": RoomAmenities(
            wifi=True, ac=True, tv=True, minibar=True, 
            balcony=True, sea_view=True, workspace=True, 
            breakfast_included=True, jacuzzi=True
        ),
        "pricing": RoomPricing(
            base_price_per_night=12000,
            weekend_surcharge_pct=15,
            discount_weekly_pct=15,
            tax_pct=15
        ),
    },
    RoomType.PRESIDENTIAL: {
        "floor_start": 9,
        "room_start": 901,
        "capacity": 4,
        "size_sqft": 800,
        "description": "Luxurious presidential suite with all amenities",
        "bed_type": BedType.KING,
        "amenities": RoomAmenities(
            wifi=True, ac=True, tv=True, minibar=True, 
            balcony=True, sea_view=True, kitchen=True, 
            workspace=True, breakfast_included=True, jacuzzi=True
        ),
        "pricing": RoomPricing(
            base_price_per_night=25000,
            weekend_surcharge_pct=20,
            discount_weekly_pct=20,
            tax_pct=15
        ),
    },
}

ROOMS_PER_TYPE = 20


def generate_rooms():
    """Generate 20 rooms for each room type"""
    all_rooms = []
    
    for room_type, config in ROOM_TYPE_CONFIGS.items():
        for i in range(ROOMS_PER_TYPE):
            room_number = str(config["room_start"] + i)
            floor = config["floor_start"] + (i // 10)  # 10 rooms per floor
            
            room_data = {
                "room_number": room_number,
                "room_type": room_type,
                "bed_type": config["bed_type"],
                "floor": floor,
                "capacity": config["capacity"],
                "size_sqft": config["size_sqft"],
                "description": config["description"],
                "amenities": config["amenities"],
                "pricing": config["pricing"],
            }
            all_rooms.append(room_data)
    
    return all_rooms


async def seed_rooms():
    """Seed database with 20 rooms per type (100 total). DELETES all existing rooms first."""
    from config import settings

    if settings.APP_ENV == "production":
        raise SystemExit(
            "Refusing to seed: APP_ENV=production. This script deletes every room. "
            "Use it only on a development or staging database."
        )
    try:
        await connect_db()
        db = get_db()
        
        # Clear existing rooms
        result = await db.rooms.delete_many({})
        logger.info(f"🗑️  Deleted {result.deleted_count} existing rooms")
        
        # Generate and insert rooms
        all_rooms = generate_rooms()
        rooms_to_insert = []
        for room_data in all_rooms:
            room = Room(**room_data)
            rooms_to_insert.append(room.model_dump(by_alias=True))
        
        result = await db.rooms.insert_many(rooms_to_insert)
        logger.info(f"✅ Seeded {len(result.inserted_ids)} rooms")
        
        # Print summary by type
        print("\n📊 Room Summary:")
        print("=" * 50)
        for room_type, config in ROOM_TYPE_CONFIGS.items():
            start = config["room_start"]
            end = start + ROOMS_PER_TYPE - 1
            price = config["pricing"].base_price_per_night
            print(
                f"  {room_type.value.upper():12} | "
                f"Rooms {start}-{end} | "
                f"BDT {price:,}/night | "
                f"{ROOMS_PER_TYPE} rooms"
            )
        print("=" * 50)
        print(f"  TOTAL: {len(all_rooms)} rooms")
        
    except Exception as e:
        logger.error(f"❌ Seeding error: {e}", exc_info=True)
        raise
    finally:
        await disconnect_db()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    asyncio.run(seed_rooms())
