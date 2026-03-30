#!/usr/bin/env python3
"""
DEBUG SCRIPT: Check MongoDB tokens for calendar integration
Helps identify field names, document structure, and token issues
"""

import os
from pymongo import MongoClient
import json

# Configuration
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017/")
MONGODB_DATABASE = os.environ.get("MONGODB_DATABASE", "genios")
MONGODB_COLLECTION = "toolaccounts"

# User to search for
TARGET_USER_ID = "691b14bce6835b6c6502d320"

print("=" * 80)
print("MongoDB Token Debug Script")
print("=" * 80)
print(f"\n📍 Configuration:")
print(f"   MongoDB URI: {MONGODB_URI}")
print(f"   Database: {MONGODB_DATABASE}")
print(f"   Collection: {MONGODB_COLLECTION}")
print(f"   Target User ID: {TARGET_USER_ID}")

try:
    # Connect to MongoDB
    print(f"\n🔗 Connecting to MongoDB...")
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    print("   ✅ Connection successful!")

    db = client[MONGODB_DATABASE]
    collection = db[MONGODB_COLLECTION]

    # Get collection stats
    print(f"\n📊 Collection Statistics:")
    doc_count = collection.count_documents({})
    print(f"   Total documents: {doc_count}")

    # ==================== QUERY 1: Exact match (current code) ====================
    print(f"\n" + "=" * 80)
    print("QUERY 1: Current code query (camelCase)")
    print("=" * 80)
    query1 = {"userId": TARGET_USER_ID, "tool": "calendar"}
    print(f"Query: {query1}")
    result1 = collection.find_one(query1)

    if result1:
        print(f"✅ FOUND!")
        print(f"Document:")
        print(json.dumps(result1, indent=2, default=str))
    else:
        print(f"❌ NOT FOUND")

    # ==================== QUERY 2: Try with user_id (snake_case) ====================
    print(f"\n" + "=" * 80)
    print("QUERY 2: Alternative query (snake_case)")
    print("=" * 80)
    query2 = {"user_id": TARGET_USER_ID, "tool": "calendar"}
    print(f"Query: {query2}")
    result2 = collection.find_one(query2)

    if result2:
        print(f"✅ FOUND!")
        print(f"Document:")
        print(json.dumps(result2, indent=2, default=str))
    else:
        print(f"❌ NOT FOUND")

    # ==================== QUERY 3: Just by user ID ====================
    print(f"\n" + "=" * 80)
    print("QUERY 3: All documents with this user ID")
    print("=" * 80)
    query3 = {"userId": TARGET_USER_ID}
    print(f"Query: {query3}")
    results3 = list(collection.find(query3))

    if results3:
        print(f"✅ FOUND {len(results3)} document(s):")
        for doc in results3:
            print(f"\nDocument:")
            print(json.dumps(doc, indent=2, default=str))
    else:
        print(f"❌ NOT FOUND with userId")

        # Try snake_case
        query3b = {"user_id": TARGET_USER_ID}
        results3b = list(collection.find(query3b))
        if results3b:
            print(f"✅ BUT FOUND with user_id ({len(results3b)} documents):")
            for doc in results3b:
                print(f"\nDocument:")
                print(json.dumps(doc, indent=2, default=str))

    # ==================== QUERY 4: Check collection schema ====================
    print(f"\n" + "=" * 80)
    print("QUERY 4: Sample documents (first 3)")
    print("=" * 80)
    samples = list(collection.find().limit(3))

    if samples:
        print(f"Found {len(samples)} sample document(s):")
        for i, doc in enumerate(samples, 1):
            print(f"\n--- Sample {i} ---")
            print(f"Keys in document: {list(doc.keys())}")
            print(f"Full document:")
            print(json.dumps(doc, indent=2, default=str))
    else:
        print("❌ Collection is empty!")

    # ==================== QUERY 5: Check for calendar tool tokens ====================
    print(f"\n" + "=" * 80)
    print("QUERY 5: Any calendar tokens in collection")
    print("=" * 80)
    query5 = {"tool": "calendar"}
    count5 = collection.count_documents(query5)
    print(f"Query: {query5}")
    print(f"Found: {count5} documents with tool='calendar'")

    if count5 > 0:
        samples5 = list(collection.find(query5).limit(3))
        print(f"\nSample calendar tokens:")
        for doc in samples5:
            print(f"\n  User: {doc.get('userId', doc.get('user_id', 'UNKNOWN'))}")
            print(f"  Tool: {doc.get('tool')}")
            print(f"  Has access_token: {'access_token' in doc}")
            print(f"  Has refresh_token: {'refresh_token' in doc}")
            print(f"  Fields: {list(doc.keys())}")

    # ==================== SUMMARY ====================
    print(f"\n" + "=" * 80)
    print("SUMMARY & RECOMMENDATIONS")
    print("=" * 80)

    if result1:
        print("✅ Token FOUND with current query!")
        print("   No action needed - system should work correctly")
    elif result2:
        print("⚠️  Token found with snake_case (user_id)")
        print("   ACTION: Update tool_tokens.py to use 'user_id' instead of 'userId'")
    elif results3 or results3b:
        print("⚠️  Token found but WITHOUT 'tool' field match")
        print("   ACTION: Check what field name is used for tool type")
    else:
        print("❌ Token NOT found for this user!")
        print("   ACTION: Insert token document into MongoDB")
        print(f"\n   Example document structure:")
        print(
            f"""{{
    "userId": "{TARGET_USER_ID}",
    "tool": "calendar",
    "access_token": "ya29.YOUR_REAL_TOKEN",
    "refresh_token": "1//YOUR_REAL_REFRESH",
    "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
    "client_secret": "YOUR_CLIENT_SECRET",
    "token_uri": "https://oauth2.googleapis.com/token",
    "scope": ["https://www.googleapis.com/auth/calendar"],
    "expires_at": 9999999999000,
    "metadata": {{
        "email": "user@example.com"
    }}
}}"""
        )

    print("\n" + "=" * 80)

except Exception as e:
    print(f"\n❌ Error: {str(e)}")
    import traceback

    traceback.print_exc()

finally:
    if "client" in locals():
        client.close()
        print("\n🔌 MongoDB connection closed")
