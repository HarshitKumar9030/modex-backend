"""
MongoDB connection via Motor (async driver).
"""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from core.config import settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def init_db():
    """Initialize the MongoDB client and create indexes."""
    global _client, _db
    _client = AsyncIOMotorClient(settings.MONGODB_URL)
    _db = _client[settings.MONGODB_DB_NAME]

    # Create indexes for efficient queries
    await _db.conversations.create_index("created_at")
    await _db.conversations.create_index("expires_at")
    await _db.messages.create_index("conversation_id")
    await _db.messages.create_index("created_at")
    await _db.files.create_index("conversation_id")
    await _db.beta_signups.create_index("email", unique=True)


def get_db() -> AsyncIOMotorDatabase:
    """FastAPI dependency — returns the MongoDB database handle."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db


async def close_db():
    """Close the MongoDB client."""
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
