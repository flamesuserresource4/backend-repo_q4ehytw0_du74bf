import os
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, EmailStr
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import Product as ProductSchema, Review as ReviewSchema, Order as OrderSchema, Newsletter as NewsletterSchema

app = FastAPI(title="TechParts Hub API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Helpers
class ObjectIdStr(str):
    @classmethod
    def validate(cls, v):
        try:
            ObjectId(v)
            return v
        except Exception:
            raise ValueError("Invalid ObjectId")

def serialize_id(value):
    if isinstance(value, ObjectId):
        return str(value)
    return value

def serialize_doc(doc: Dict[str, Any]):
    if not doc:
        return doc
    doc = dict(doc)
    doc["id"] = serialize_id(doc.pop("_id", None))
    # Convert potential nested ids
    for k, v in list(doc.items()):
        if isinstance(v, ObjectId):
            doc[k] = str(v)
    return doc


# Request models
class ReviewIn(BaseModel):
    user_name: str
    rating: int = Field(ge=1, le=5)
    comment: Optional[str] = ""

class WishlistToggleIn(BaseModel):
    email: EmailStr
    product_id: str

class CartItemIn(BaseModel):
    product_id: str
    quantity: int = Field(ge=1)

class EstimateIn(BaseModel):
    items: List[CartItemIn]
    country: Optional[str] = "US"
    state: Optional[str] = None
    postal_code: Optional[str] = None

class CheckoutIn(BaseModel):
    customer_name: str
    email: EmailStr
    shipping_address: str
    payment_method: str
    items: List[CartItemIn]
    notes: Optional[str] = None


@app.get("/")
def root():
    return {"message": "TechParts Hub API running"}


# Products endpoints
@app.get("/api/products")
def list_products(
    q: Optional[str] = None,
    category: Optional[str] = None,
    brand: Optional[str] = None,
    minPrice: Optional[float] = None,
    maxPrice: Optional[float] = None,
    rating: Optional[float] = None,
    sort: Optional[str] = Query("newest", description="price_asc|price_desc|newest|rating_desc"),
    page: int = 1,
    limit: int = 20,
):
    query: Dict[str, Any] = {}
    if q:
        query["$or"] = [
            {"title": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
            {"tags": {"$regex": q, "$options": "i"}},
        ]
    if category:
        query["category"] = {"$regex": f"^{category}$", "$options": "i"}
    if brand:
        query["brand"] = {"$regex": f"^{brand}$", "$options": "i"}
    if minPrice is not None or maxPrice is not None:
        price_filter: Dict[str, Any] = {}
        if minPrice is not None:
            price_filter["$gte"] = minPrice
        if maxPrice is not None:
            price_filter["$lte"] = maxPrice
        query["price"] = price_filter
    if rating is not None:
        query["rating"] = {"$gte": rating}

    sort_map = {
        "price_asc": ("price", 1),
        "price_desc": ("price", -1),
        "newest": ("created_at", -1),
        "rating_desc": ("rating", -1),
    }
    sort_field, sort_dir = sort_map.get(sort, ("created_at", -1))

    skip = max(0, (page - 1) * limit)

    cursor = db["product"].find(query).sort(sort_field, sort_dir).skip(skip).limit(limit)
    items = [serialize_doc(x) for x in cursor]
    total = db["product"].count_documents(query)
    return {"items": items, "total": total, "page": page, "limit": limit}


@app.get("/api/search/suggestions")
def search_suggestions(q: str):
    if not q:
        return []
    cursor = db["product"].find(
        {"$or": [
            {"title": {"$regex": q, "$options": "i"}},
            {"tags": {"$regex": q, "$options": "i"}},
        ]},
        {"title": 1}
    ).limit(10)
    seen = set()
    suggestions: List[str] = []
    for doc in cursor:
        t = doc.get("title", "")
        if t and t not in seen:
            seen.add(t)
            suggestions.append(t)
    return suggestions


@app.get("/api/products/new")
def new_arrivals(limit: int = 8):
    cursor = db["product"].find({"is_new": True}).sort("created_at", -1).limit(limit)
    return [serialize_doc(x) for x in cursor]


@app.get("/api/products/best")
def best_sellers(limit: int = 8):
    cursor = db["product"].find({"is_best_seller": True}).sort("reviews_count", -1).limit(limit)
    return [serialize_doc(x) for x in cursor]


@app.get("/api/products/{product_id}")
def get_product(product_id: str):
    try:
        doc = db["product"].find_one({"_id": ObjectId(product_id)})
    except Exception:
        raise HTTPException(404, "Product not found")
    if not doc:
        raise HTTPException(404, "Product not found")
    return serialize_doc(doc)


@app.get("/api/products/{product_id}/reviews")
def get_reviews(product_id: str):
    cursor = db["review"].find({"product_id": product_id}).sort("created_at", -1)
    return [serialize_doc(x) for x in cursor]


@app.post("/api/products/{product_id}/reviews")
def add_review(product_id: str, payload: ReviewIn):
    data = ReviewSchema(product_id=product_id, user_name=payload.user_name, rating=payload.rating, comment=payload.comment)
    data.created_at = data.created_at or None
    inserted_id = create_document("review", data)

    # Update product aggregate
    try:
        prod = db["product"].find_one({"_id": ObjectId(product_id)})
        if prod:
            count = db["review"].count_documents({"product_id": product_id})
            avg = db["review"].aggregate([
                {"$match": {"product_id": product_id}},
                {"$group": {"_id": None, "avg": {"$avg": "$rating"}}}
            ])
            avg_val = 0
            for a in avg:
                avg_val = a.get("avg", 0)
            db["product"].update_one({"_id": ObjectId(product_id)}, {"$set": {"reviews_count": count, "rating": round(avg_val, 2)}})
    except Exception:
        pass

    return {"id": inserted_id, "status": "ok"}


# Wishlist endpoints
@app.post("/api/wishlist/toggle")
def wishlist_toggle(data: WishlistToggleIn):
    wl = db["wishlist"].find_one({"email": data.email})
    if not wl:
        db["wishlist"].insert_one({"email": data.email, "product_ids": [data.product_id]})
        return {"action": "added", "product_id": data.product_id}
    product_ids: List[str] = wl.get("product_ids", [])
    if data.product_id in product_ids:
        product_ids = [pid for pid in product_ids if pid != data.product_id]
        db["wishlist"].update_one({"email": data.email}, {"$set": {"product_ids": product_ids}})
        return {"action": "removed", "product_id": data.product_id}
    else:
        product_ids.append(data.product_id)
        db["wishlist"].update_one({"email": data.email}, {"$set": {"product_ids": product_ids}})
        return {"action": "added", "product_id": data.product_id}


@app.get("/api/wishlist")
def get_wishlist(email: EmailStr):
    wl = db["wishlist"].find_one({"email": str(email)})
    if not wl:
        return {"email": str(email), "product_ids": []}
    return {"email": wl.get("email"), "product_ids": wl.get("product_ids", [])}


# Cart and checkout
@app.post("/api/cart/estimate")
def estimate_totals(payload: EstimateIn):
    subtotal = 0.0
    details: List[Dict[str, Any]] = []
    for item in payload.items:
        try:
            prod = db["product"].find_one({"_id": ObjectId(item.product_id)})
            if not prod:
                raise Exception("Product not found")
            price = float(prod.get("price", 0))
            line_total = price * item.quantity
            subtotal += line_total
            details.append({"product_id": item.product_id, "title": prod.get("title"), "price": price, "quantity": item.quantity, "line_total": round(line_total, 2)})
        except Exception:
            continue
    shipping = 0 if subtotal >= 99 else 7.99
    tax_rate = 0.07 if (payload.country == "US") else 0.0
    tax = subtotal * tax_rate
    total = subtotal + shipping + tax
    return {
        "items": details,
        "subtotal": round(subtotal, 2),
        "shipping": round(shipping, 2),
        "tax": round(tax, 2),
        "total": round(total, 2),
    }


@app.post("/api/checkout")
def checkout(payload: CheckoutIn):
    # Build order items snapshot
    estimate = estimate_totals(EstimateIn(items=payload.items))
    order = OrderSchema(
        customer_name=payload.customer_name,
        email=payload.email,
        shipping_address=payload.shipping_address,
        payment_method=payload.payment_method,  # Demo only
        items=[
            {
                "product_id": it["product_id"],
                "title": it["title"],
                "price": it["price"],
                "quantity": it["quantity"],
            }
            for it in estimate["items"]
        ],
        subtotal=estimate["subtotal"],
        shipping=estimate["shipping"],
        tax=estimate["tax"],
        total=estimate["total"],
        notes=payload.notes,
    )
    order_id = create_document("order", order)
    return {"order_id": order_id, "status": "confirmed"}


# Newsletter subscription
@app.post("/api/newsletter")
def newsletter_subscribe(data: NewsletterSchema):
    exists = db["newsletter"].find_one({"email": data.email})
    if exists:
        return {"status": "already_subscribed"}
    _id = create_document("newsletter", data)
    return {"status": "subscribed", "id": _id}


# Seeder endpoint to populate sample products
@app.post("/api/seed")
def seed_products():
    count = db["product"].count_documents({})
    if count > 0:
        return {"status": "already_seeded", "count": count}

    samples: List[ProductSchema] = [
        ProductSchema(
            title="Arduino Uno R3",
            description="Classic Arduino-compatible microcontroller board for beginners and makers.",
            price=24.99,
            category="Microcontrollers",
            brand="Arduino",
            images=[
                "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?q=80&w=1200&auto=format&fit=crop",
            ],
            specs={"MCU": "ATmega328P", "I/O Pins": 14, "USB": "Type-B"},
            tags=["arduino", "microcontroller", "uno"],
            is_new=True,
            is_best_seller=True,
            discount_percent=10,
        ),
        ProductSchema(
            title="Raspberry Pi 4 Model B 4GB",
            description="Powerful single-board computer ideal for projects and learning.",
            price=74.99,
            category="Microcontrollers",
            brand="Raspberry Pi",
            images=[
                "https://images.unsplash.com/photo-1518770660439-4636190af475?q=80&w=1200&auto=format&fit=crop",
            ],
            specs={"RAM": "4GB", "USB": "USB3", "CPU": "Quad-core"},
            tags=["raspberry", "pi", "sbc"],
            is_new=True,
        ),
        ProductSchema(
            title="ESP32 DevKit",
            description="WiFi + Bluetooth microcontroller module for IoT applications.",
            price=9.99,
            category="Microcontrollers",
            brand="Espressif",
            images=[
                "https://images.unsplash.com/photo-1518779578993-ec3579fee39f?q=80&w=1200&auto=format&fit=crop",
            ],
            specs={"WiFi": "802.11n", "Bluetooth": "v4.2", "GPIO": 34},
            tags=["esp32", "iot"],
            is_best_seller=True,
        ),
        ProductSchema(
            title="Soldering Iron Kit 60W",
            description="Adjustable temperature soldering iron with tips and stand.",
            price=19.99,
            category="Tools",
            brand="Hakko",
            images=[
                "https://images.unsplash.com/photo-1581091226825-c6a5f0c39bfe?q=80&w=1200&auto=format&fit=crop",
            ],
            specs={"Power": "60W", "Temp": "200-450°C"},
            tags=["soldering", "tools"],
        ),
        ProductSchema(
            title="NVMe SSD 1TB",
            description="High-speed PCIe Gen3 NVMe SSD for desktops and laptops.",
            price=79.99,
            category="PC Components",
            brand="Samsung",
            images=[
                "https://images.unsplash.com/photo-1517336714731-489689fd1ca8?q=80&w=1200&auto=format&fit=crop",
            ],
            specs={"Capacity": "1TB", "Interface": "M.2 NVMe"},
            tags=["ssd", "storage"],
            is_best_seller=True,
        ),
        ProductSchema(
            title="Brushless DC Motor",
            description="High torque BLDC motor for robotics and drones.",
            price=29.99,
            category="Robotics",
            brand="T-Motor",
            images=[
                "https://images.unsplash.com/photo-1605972153457-8b0b0cf66f60?q=80&w=1200&auto=format&fit=crop",
            ],
            specs={"KV": 920, "Voltage": "12V"},
            tags=["motor", "robotics"],
        ),
        ProductSchema(
            title="Assorted Jumper Wires Kit",
            description="Dupont jumper wires for breadboards and prototyping.",
            price=5.99,
            category="Components",
            brand="TechParts",
            images=[
                "https://images.unsplash.com/photo-1516387938699-a93567ec168e?q=80&w=1200&auto=format&fit=crop",
            ],
            specs={"Lengths": "10/20/30cm", "Qty": 120},
            tags=["wires", "dupont", "breadboard"],
            is_new=True,
        ),
        ProductSchema(
            title="Smart Home DIY Kit",
            description="Build an IoT smart home with sensors and ESP32.",
            price=49.99,
            category="DIY Kits",
            brand="TechParts",
            images=[
                "https://images.unsplash.com/photo-1518770660439-4636190af475?q=80&w=1200&auto=format&fit=crop",
            ],
            specs={"Controller": "ESP32", "Sensors": 8},
            tags=["kit", "iot", "diy"],
            discount_percent=15,
        ),
    ]

    inserted = 0
    for p in samples:
        create_document("product", p)
        inserted += 1

    return {"status": "seeded", "inserted": inserted}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
