"""
Database Schemas for TechParts Hub

Each Pydantic model maps to a MongoDB collection using its lowercase class name.
Example: Product -> "product" collection
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal
from datetime import datetime

class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Email address")
    address: Optional[str] = Field(None, description="Address")
    is_active: bool = Field(True, description="Whether user is active")

class Product(BaseModel):
    title: str = Field(..., description="Product title")
    description: Optional[str] = Field(None, description="Product description")
    price: float = Field(..., ge=0, description="Price in USD")
    category: str = Field(..., description="Primary category")
    brand: Optional[str] = Field(None, description="Brand name")
    rating: float = Field(4.5, ge=0, le=5, description="Average rating")
    reviews_count: int = Field(0, ge=0, description="Number of reviews")
    images: List[str] = Field(default_factory=list, description="Image URLs")
    specs: dict = Field(default_factory=dict, description="Key/value technical specs")
    in_stock: bool = Field(True, description="Whether product is in stock")
    tags: List[str] = Field(default_factory=list, description="Searchable tags")
    is_new: bool = Field(False, description="New arrival flag")
    is_best_seller: bool = Field(False, description="Best seller flag")
    discount_percent: Optional[int] = Field(None, ge=0, le=90, description="Optional discount percent")

class Review(BaseModel):
    product_id: str = Field(..., description="Related product id as string")
    user_name: str = Field(..., description="Reviewer display name")
    rating: int = Field(..., ge=1, le=5, description="Rating 1-5")
    comment: Optional[str] = Field("", description="Review text")
    created_at: Optional[datetime] = None

class OrderItem(BaseModel):
    product_id: str
    title: str
    price: float
    quantity: int

class Order(BaseModel):
    customer_name: str
    email: EmailStr
    shipping_address: str
    payment_method: Literal['card','cod','paypal']
    items: List[OrderItem]
    subtotal: float
    shipping: float
    tax: float
    total: float
    notes: Optional[str] = None
    created_at: Optional[datetime] = None

class Newsletter(BaseModel):
    email: EmailStr
