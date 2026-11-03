import os
import hmac
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from gridfs import GridFSBucket
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from starlette.concurrency import run_in_threadpool

from .auth import (
    create_invitation_code,
    create_session_token,
    decode_session_token,
    hash_invitation_code,
    hash_password,
    verify_password,
)
from .services import assess_priority, classify, distance_m, route

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.getenv("MONGO_DB", "civicops")
client = None
db = None
database_ready = False
logger = logging.getLogger(__name__)
database_lock = threading.Lock()
app = FastAPI(title="CiviCops API", version="1.0.0", description="Civic issue reporting and resolution API")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","), allow_methods=["*"], allow_headers=["Authorization", "Content-Type"])
bearer = HTTPBearer(auto_error=False)
DEPARTMENTS = {"Public Works", "Sanitation", "Water Management", "Electrical Services", "Drainage"}
MAX_MEDIA_SIZE = 20 * 1024 * 1024
ALLOWED_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp"}

class Location(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    address: str = ""

class MediaReference(BaseModel):
    media_id: str
    filename: str = Field(min_length=1, max_length=255)
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    size: int = Field(gt=0, le=MAX_MEDIA_SIZE)

class ReportCreate(BaseModel):
    title: str = Field(min_length=4, max_length=140)
    description: str = Field(min_length=8, max_length=4000)
    category: str = "Other"
    location: Location
    severity: int = Field(default=3, ge=1, le=5)
    urgency: int = Field(default=3, ge=1, le=5)
    media: list[MediaReference] = Field(default_factory=list, max_length=5)

class StatusUpdate(BaseModel):
    status: Literal["Acknowledged", "Assigned", "In progress", "Resolved", "Closed"]
    note: str = ""

class AuthRegistration(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=128)
    access_code: str | None = Field(default=None, max_length=256)

class AuthLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

class InvitationCreate(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["Citizen", "Worker", "Admin"]
    department: str | None = None

class StaffDepartmentUpdate(BaseModel):
    department: str | None = None

class ReportAssignment(BaseModel):
    worker_id: str

def role_domains():
    domains = {
        "Admin": os.getenv("AUTH_ADMIN_DOMAIN", "").strip().lower().lstrip("@"),
        "Worker": os.getenv("AUTH_WORKER_DOMAIN", "").strip().lower().lstrip("@"),
        "Citizen": os.getenv("AUTH_CITIZEN_DOMAIN", "").strip().lower().lstrip("@"),
    }
    domain_pattern = r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
    if any(not re.fullmatch(domain_pattern, domain) for domain in domains.values()):
        raise HTTPException(503, "Role domains must contain only hostnames, such as admin.example.com, not email addresses")
    if len(set(domains.values())) != len(domains):
        raise HTTPException(503, "Each account role must use a different email domain")
    return domains

def role_for_email(email):
    domain = email.rpartition("@")[2]
    return next((role for role, configured_domain in role_domains().items() if domain == configured_domain), None)

def normalized_email(email):
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(422, "Enter a valid email address")
    return email

def public_user(user):
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "name": user.get("name", user["email"]),
        "role": user["role"],
        "departments": user.get("departments", []),
    }

def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not credentials:
        raise HTTPException(401, "Sign in is required", headers={"WWW-Authenticate": "Bearer"})
    user_id = decode_session_token(credentials.credentials)
    user = db.users.find_one({"user_id": user_id})
    if not user:
        raise HTTPException(401, "Account is no longer active; sign in again", headers={"WWW-Authenticate": "Bearer"})
    return user

def require_admin(user):
    if user["role"] != "Admin":
        raise HTTPException(403, "Administrator access required")

def serialize(report):
    if not report: return None
    report.setdefault("upvotes", report_vote_summaries([report["report_id"]]).get(report["report_id"], {}).get("count", 0))
    if "priority_score" in report or "priority" in report:
        report["priority_score"], report["priority"] = priority_with_upvotes(report)
    report["_id"] = str(report["_id"])
    return report

def priority_with_upvotes(report):
    base_score = report.get("priority_score")
    if base_score is None:
        base_score = {"Low": 44, "Medium": 45, "High": 70}.get(report.get("priority"), 0)
    score = min(100, base_score + min(report.get("upvotes", 0), 20))
    priority = "High" if score >= 70 else "Medium" if score >= 45 else "Low"
    return score, priority

def report_vote_summaries(report_ids, user_id=None):
    if not report_ids:
        return {}
    rows = db.report_votes.aggregate([
        {"$match": {"report_id": {"$in": report_ids}}},
        {"$group": {
            "_id": "$report_id",
            "count": {"$sum": 1},
            "upvoted": {"$max": {"$cond": [{"$eq": ["$user_id", user_id]}, 1, 0]}},
        }},
    ])
    return {row["_id"]: {"count": row["count"], "upvoted": bool(row["upvoted"])} for row in rows}

def serialize_public_report(report):
    upvotes = report.get("upvotes", 0)
    _, priority = priority_with_upvotes({**report, "upvotes": upvotes})
    return {
        "report_id": report["report_id"],
        "title": report["title"],
        "description": report.get("description", ""),
        "category": report.get("category", "Other"),
        "ai_category": report.get("ai_category"),
        "status": report.get("status", "Submitted"),
        "priority": priority,
        "upvotes": upvotes,
        "upvoted": report.get("upvoted", False),
        "location": report.get("location", {}),
        "created_at": report.get("created_at"),
        "updated_at": report.get("updated_at"),
        "status_history": [
            {"status": update["status"], "at": update.get("at")}
            for update in report.get("status_history", [])
            if update.get("status")
        ],
    }

def create_indexes():
    db.reports.create_index("report_id", unique=True)
    db.reports.create_index([("location.latitude", 1), ("location.longitude", 1)])
    db.reports.create_index([("status", 1), ("priority", 1), ("created_at", -1)])
    db.reports.create_index("assigned_staff")
    db.notifications.create_index([("user_id", 1), ("created_at", -1)])
    db.users.create_index("user_id", unique=True, partialFilterExpression={"user_id": {"$type": "string"}})
    db.users.create_index("email", unique=True, partialFilterExpression={"email": {"$type": "string"}})
    db.invitations.create_index("token_hash", unique=True)
    db.invitations.create_index("expires_at", expireAfterSeconds=0)
    db.report_votes.create_index([("report_id", 1), ("user_id", 1)], unique=True)
    db.report_votes.create_index("report_id")

def initialize_database():
    global client, db, database_ready
    if database_ready:
        return
    with database_lock:
        if database_ready:
            return
        if client is None:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
            db = client[DB_NAME]
        client.admin.command("ping")
        if not database_ready:
            create_indexes()
            database_ready = True

@app.middleware("http")
async def require_database(request, call_next):
    if request.url.path != "/api/health":
        try:
            await run_in_threadpool(initialize_database)
        except PyMongoError as exc:
            logger.warning("MongoDB is unavailable for request %s: %s", request.url.path, exc)
            return JSONResponse(
                status_code=503,
                content={"detail": "Database is temporarily unavailable. Check MongoDB DNS/network access and retry."},
            )
    return await call_next(request)

@app.get("/api/health")
def health():
    try:
        initialize_database()
        client.admin.command("ping")
        return {"status": "ok", "database": "connected"}
    except PyMongoError as exc:
        logger.warning("MongoDB health check failed: %s", exc)
        return {"status": "degraded", "database": "unavailable", "detail": "MongoDB DNS resolution or network connection failed"}

@app.post("/api/auth/register", status_code=201)
def register(payload: AuthRegistration):
    email = normalized_email(payload.email)
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, "Enter your name")
    existing = db.users.find_one({"email": email})
    role = role_for_email(email)
    if role is None and existing and existing.get("role") == "Citizen":
        role = "Citizen"
    if role is None:
        raise HTTPException(403, "This email domain is not enabled for CiviCops accounts")
    if existing and existing.get("password_hash"):
        raise HTTPException(409, "An account already exists for this email; sign in instead")

    access_code = (payload.access_code or "").strip()
    bootstrap_secret = os.getenv("BOOTSTRAP_ADMIN_TOKEN", "")
    bootstrap_admin = (
        role == "Admin"
        and not db.users.count_documents({"role": "Admin", "password_hash": {"$exists": True}})
        and len(bootstrap_secret.encode("utf-8")) >= 32
        and bool(access_code)
        and hmac.compare_digest(access_code, bootstrap_secret)
    )
    invitation = None
    if role in {"Admin", "Worker"} or existing:
        if not bootstrap_admin:
            if not access_code:
                raise HTTPException(403, "An administrator invitation is required for this account")
            invitation = db.invitations.find_one_and_update(
                {
                    "token_hash": hash_invitation_code(access_code),
                    "email": email,
                    "role": role,
                    "used_at": None,
                    "expires_at": {"$gt": datetime.now(timezone.utc)},
                },
                {"$set": {"used_at": datetime.now(timezone.utc)}},
                return_document=ReturnDocument.AFTER,
            )
            if invitation is None:
                raise HTTPException(403, "The invitation is invalid, expired, or already used")

    now = datetime.now(timezone.utc)
    user_id = existing.get("user_id") if existing else None
    user_id = user_id or str(uuid4())
    if bootstrap_admin:
        role = "Admin"
    departments = invitation.get("departments", []) if invitation else []
    account = {
        "user_id": user_id,
        "email": email,
        "name": name,
        "role": role,
        "departments": departments,
        "password_hash": hash_password(payload.password),
        "last_login": now,
    }
    if existing:
        account.pop("last_login")
        account["created_at"] = existing.get("created_at", now)
        user_filter = {"_id": existing["_id"]} if "_id" in existing else {"email": email}
        db.users.update_one(user_filter, {"$set": account})
    else:
        account["created_at"] = now
        db.users.insert_one(account)
    user = db.users.find_one({"user_id": user_id})
    return {"access_token": create_session_token(user_id), "user": public_user(user)}

@app.post("/api/auth/login")
def login(payload: AuthLogin):
    email = normalized_email(payload.email)
    user = db.users.find_one({"email": email})
    if not user or not verify_password(payload.password, user.get("password_hash")):
        raise HTTPException(401, "Email or password is incorrect")
    db.users.update_one({"user_id": user["user_id"]}, {"$set": {"last_login": datetime.now(timezone.utc)}})
    user["last_login"] = datetime.now(timezone.utc)
    return {"access_token": create_session_token(user["user_id"]), "user": public_user(user)}

@app.post("/api/admin/invitations", status_code=201)
def create_invitation(payload: InvitationCreate, user=Depends(get_current_user)):
    require_admin(user)
    email = normalized_email(payload.email)
    existing = db.users.find_one({"email": email})
    if existing and existing.get("password_hash"):
        raise HTTPException(409, "This account already has a password")
    if role_for_email(email) != payload.role:
        legacy_citizen = (
            payload.role == "Citizen"
            and role_for_email(email) is None
            and existing is not None
            and existing.get("role") == "Citizen"
        )
        if not legacy_citizen:
            raise HTTPException(422, "The email domain does not match the selected role")
    if payload.role == "Worker" and payload.department not in DEPARTMENTS:
        raise HTTPException(422, "Choose a valid department for a worker")
    if payload.role != "Worker" and payload.department:
        raise HTTPException(422, "A department can only be set for a worker")
    code = create_invitation_code()
    now = datetime.now(timezone.utc)
    db.invitations.insert_one({
        "token_hash": hash_invitation_code(code),
        "email": email,
        "role": payload.role,
        "departments": [payload.department] if payload.role == "Worker" else [],
        "created_by": user["user_id"],
        "created_at": now,
        "expires_at": now + timedelta(hours=72),
        "used_at": None,
    })
    return {"email": email, "role": payload.role, "code": code, "expires_at": now + timedelta(hours=72)}

@app.get("/api/auth/me")
def get_session(user=Depends(get_current_user)):
    return public_user(user)

@app.get("/api/reports")
def list_reports(status: str | None = None, category: str | None = None, q: str | None = None,
                 limit: int = Query(default=100, le=200), user=Depends(get_current_user)):
    query = {}
    if status: query["status"] = status
    if category: query["category"] = category
    if q: query["$or"] = [{"title": {"$regex": q, "$options": "i"}}, {"description": {"$regex": q, "$options": "i"}}, {"report_id": {"$regex": q, "$options": "i"}}]
    if user["role"] == "Citizen": query["citizen_id"] = user["user_id"]
    if user["role"] == "Worker": query["assigned_staff"] = user["user_id"]
    items = list(db.reports.find(query).sort("created_at", -1).limit(limit))
    summaries = report_vote_summaries([item["report_id"] for item in items])
    for item in items:
        item["upvotes"] = summaries.get(item["report_id"], {}).get("count", 0)
    if user["role"] == "Admin":
        items.sort(key=lambda item: (
            item.get("status") not in {"Resolved", "Closed"},
            priority_with_upvotes(item)[0],
            item.get("upvotes", 0),
            item.get("created_at", datetime.min.replace(tzinfo=timezone.utc)),
        ), reverse=True)
    return [serialize(item) for item in items]

@app.get("/api/community/reports")
def list_community_reports(status: str | None = None, category: str | None = None,
                           q: str | None = None, limit: int = Query(default=100, le=200),
                           user=Depends(get_current_user)):
    if user["role"] != "Citizen":
        raise HTTPException(403, "Community report access is for citizens")
    query = {"citizen_id": {"$ne": user["user_id"]}}
    if status: query["status"] = status
    if category: query["category"] = category
    if q: query["$or"] = [{"title": {"$regex": q, "$options": "i"}}, {"description": {"$regex": q, "$options": "i"}}, {"report_id": {"$regex": q, "$options": "i"}}]
    items = list(db.reports.find(query).sort("created_at", -1).limit(limit))
    summaries = report_vote_summaries([item["report_id"] for item in items], user["user_id"])
    for item in items:
        summary = summaries.get(item["report_id"], {})
        item["upvotes"] = summary.get("count", 0)
        item["upvoted"] = summary.get("upvoted", False)
    return [serialize_public_report(item) for item in items]

@app.post("/api/community/reports/{report_id}/upvote")
def upvote_community_report(report_id: str, user=Depends(get_current_user)):
    if user["role"] != "Citizen":
        raise HTTPException(403, "Community report voting is for citizens")
    report = db.reports.find_one({"report_id": report_id})
    if not report:
        raise HTTPException(404, "Report not found")
    if report.get("citizen_id") == user["user_id"]:
        raise HTTPException(403, "You cannot upvote your own report")
    if report.get("status") in {"Resolved", "Closed"}:
        raise HTTPException(409, "Resolved reports can no longer be upvoted")
    try:
        db.report_votes.insert_one({
            "report_id": report_id,
            "user_id": user["user_id"],
            "created_at": datetime.now(timezone.utc),
        })
    except DuplicateKeyError:
        pass
    summary = report_vote_summaries([report_id]).get(report_id, {})
    upvotes = summary.get("count", 0)
    priority_score, priority = priority_with_upvotes({**report, "upvotes": upvotes})
    return {
        "report_id": report_id,
        "upvotes": upvotes,
        "upvoted": True,
        "priority": priority,
        "priority_score": priority_score,
    }

@app.post("/api/reports", status_code=201)
def create_report(payload: ReportCreate, user=Depends(get_current_user)):
    if user["role"] != "Citizen": raise HTTPException(403, "Only citizens can submit reports")
    media = []
    for item in payload.media:
        try:
            media_id = ObjectId(item.media_id)
        except InvalidId as exc:
            raise HTTPException(422, "An uploaded image reference is invalid") from exc
        stored_file = db.fs.files.find_one({
            "_id": media_id,
            "metadata.owner_id": user["user_id"],
            "metadata.report_id": {"$exists": False},
        })
        if not stored_file:
            raise HTTPException(422, "An uploaded image is no longer available")
        metadata = stored_file.get("metadata", {})
        if metadata.get("content_type") != item.content_type or stored_file.get("length") != item.size:
            raise HTTPException(422, "Uploaded image details do not match")
        media.append(item.model_dump())
    classification, confidence = classify(payload.category, payload.description)
    related = []
    for other in db.reports.find({"category": classification, "status": {"$nin": ["Resolved", "Closed"]}, "created_at": {"$gte": datetime.now(timezone.utc)-timedelta(days=7)}}):
        loc = other.get("location", {})
        if loc and distance_m(payload.location.latitude, payload.location.longitude, loc.get("latitude", 0), loc.get("longitude", 0)) < 250:
            related.append(other["report_id"])
    score, priority = assess_priority(classification, payload.description, len(related))
    now = datetime.now(timezone.utc)
    report_id = f"CC-{now.year}-{uuid4().hex[:6].upper()}"
    report = {"report_id": report_id, "citizen_id": user["user_id"], "citizen_email": user["email"], "title": payload.title,
        "description": payload.description, "media": media, "category": payload.category,
        "ai_category": classification, "ai_confidence": confidence, "needs_review": confidence < .6,
        "location": payload.location.model_dump(), "severity": payload.severity, "urgency": payload.urgency,
        "priority_score": score, "priority": priority, "department": route(classification, payload.location.latitude, payload.location.longitude),
        "assigned_staff": None, "assigned_staff_name": None, "status": "Submitted", "related_ids": related, "created_at": now, "updated_at": now,
        "status_history": [{"status": "Submitted", "at": now, "by": user["user_id"], "note": "Report received"}], "resolution": None}
    for item in media:
        db.fs.files.update_one(
            {"_id": ObjectId(item["media_id"]), "metadata.owner_id": user["user_id"], "metadata.report_id": {"$exists": False}},
            {"$set": {"metadata.report_id": report_id}},
        )
    db.reports.insert_one(report)
    db.notifications.insert_one({"user_id": user["user_id"], "report_id": report["report_id"], "message": "Your report was submitted successfully", "created_at": now, "read": False})
    return serialize(report)

@app.post("/api/media", status_code=201)
def upload_media(file: UploadFile = File(...), user=Depends(get_current_user)):
    if user["role"] != "Citizen":
        raise HTTPException(403, "Only citizens can upload report images")
    if file.content_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(415, "Upload a JPG, PNG, or WebP image")
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    if size <= 0 or size > MAX_MEDIA_SIZE:
        raise HTTPException(413, "Image must be 20 MB or smaller")
    media_id = GridFSBucket(db).upload_from_stream(
        Path(file.filename or "report-image").name,
        file.file,
        metadata={"owner_id": user["user_id"], "content_type": file.content_type},
    )
    return {
        "media_id": str(media_id),
        "filename": Path(file.filename or "report-image").name,
        "content_type": file.content_type,
        "size": size,
    }

@app.delete("/api/media/{media_id}", status_code=204)
def delete_unattached_media(media_id: str, user=Depends(get_current_user)):
    try:
        object_id = ObjectId(media_id)
    except InvalidId as exc:
        raise HTTPException(404, "Image not found") from exc
    metadata = db.fs.files.find_one({"_id": object_id, "metadata.owner_id": user["user_id"]})
    if not metadata:
        raise HTTPException(404, "Image not found")
    if metadata.get("metadata", {}).get("report_id"):
        raise HTTPException(409, "Image is already attached to a report")
    GridFSBucket(db).delete(object_id)

@app.get("/api/media/{media_id}")
def get_report_media(media_id: str, user=Depends(get_current_user)):
    try:
        object_id = ObjectId(media_id)
    except InvalidId as exc:
        raise HTTPException(404, "Image not found") from exc
    stored_file = db.fs.files.find_one({"_id": object_id})
    if not stored_file:
        raise HTTPException(404, "Image not found")
    report = db.reports.find_one({"report_id": stored_file.get("metadata", {}).get("report_id")})
    if not report:
        raise HTTPException(404, "Image not found")
    allowed = (
        user["role"] == "Admin"
        or (user["role"] == "Citizen" and report.get("citizen_id") == user["user_id"])
        or (user["role"] == "Worker" and report.get("assigned_staff") == user["user_id"])
    )
    if not allowed:
        raise HTTPException(404, "Image not found")
    content_type = stored_file.get("metadata", {}).get("content_type")
    if content_type not in ALLOWED_MEDIA_TYPES:
        raise HTTPException(415, "Stored file is not a supported image")
    image = GridFSBucket(db).open_download_stream(object_id)

    def image_chunks():
        try:
            while True:
                chunk = image.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            image.close()

    return StreamingResponse(
        image_chunks(),
        media_type=content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )

@app.get("/api/reports/{report_id}")
def get_report(report_id: str, user=Depends(get_current_user)):
    report = db.reports.find_one({"report_id": report_id})
    if not report: raise HTTPException(404, "Report not found")
    summary = report_vote_summaries([report_id], user["user_id"]).get(report_id, {})
    report["upvotes"] = summary.get("count", 0)
    report["upvoted"] = summary.get("upvoted", False)
    if user["role"] == "Citizen" and report["citizen_id"] != user["user_id"]:
        return serialize_public_report(report)
    if user["role"] == "Worker" and report.get("assigned_staff") != user["user_id"]: raise HTTPException(404, "Report not found")
    return serialize(report)

@app.post("/api/reports/{report_id}/assign")
def assign_report(report_id: str, payload: ReportAssignment, user=Depends(get_current_user)):
    require_admin(user)
    report = db.reports.find_one({"report_id": report_id})
    if not report: raise HTTPException(404, "Report not found")
    worker = db.users.find_one({"user_id": payload.worker_id, "role": "Worker"})
    if not worker: raise HTTPException(404, "Worker account not found")
    if report["department"] not in worker.get("departments", []):
        raise HTTPException(409, "Worker is not assigned to this report's department")
    now = datetime.now(timezone.utc)
    db.reports.update_one(
        {"report_id": report_id},
        {"$set": {"assigned_staff": worker["user_id"], "assigned_staff_name": worker.get("name", worker["email"]), "status": "Assigned", "updated_at": now},
         "$push": {"status_history": {"status": "Assigned", "at": now, "by": user["user_id"], "note": f"Assigned to {worker.get('name', worker['email'])}"}}},
    )
    db.notifications.insert_one({"user_id": worker["user_id"], "report_id": report_id, "message": f"New report assigned: {report['title']}", "created_at": now, "read": False})
    db.notifications.insert_one({"user_id": report["citizen_id"], "report_id": report_id, "message": "A city worker has been assigned to your report", "created_at": now, "read": False})
    return serialize(db.reports.find_one({"report_id": report_id}))

@app.patch("/api/reports/{report_id}/status")
def update_status(report_id: str, payload: StatusUpdate, user=Depends(get_current_user)):
    if user["role"] not in {"Admin", "Worker"}: raise HTTPException(403, "Staff access required")
    existing = db.reports.find_one({"report_id": report_id})
    if not existing: raise HTTPException(404, "Report not found")
    if user["role"] == "Worker":
        if existing.get("assigned_staff") != user["user_id"]: raise HTTPException(403, "Report is not assigned to you")
        if payload.status not in {"In progress", "Resolved"}: raise HTTPException(403, "Workers can only mark assigned reports In progress or Resolved")
    now = datetime.now(timezone.utc)
    db.reports.update_one({"report_id": report_id}, {"$set": {"status": payload.status, "updated_at": now}, "$push": {"status_history": {"status": payload.status, "at": now, "by": user["user_id"], "note": payload.note}}})
    db.notifications.insert_one({"user_id": existing["citizen_id"], "report_id": report_id, "message": f"Your report status changed to {payload.status}", "created_at": now, "read": False})
    return serialize(db.reports.find_one({"report_id": report_id}))

@app.get("/api/admin/users")
def admin_users(user=Depends(get_current_user)):
    require_admin(user)
    return [public_user(item) for item in db.users.find({"email": {"$type": "string"}, "user_id": {"$type": "string"}}, {"_id": 0}).sort("email", 1)]

@app.patch("/api/admin/users/{user_id}/department")
def update_user_department(user_id: str, payload: StaffDepartmentUpdate, user=Depends(get_current_user)):
    require_admin(user)
    target = db.users.find_one({"user_id": user_id})
    if not target: raise HTTPException(404, "User account not found")
    if target["role"] != "Worker":
        raise HTTPException(422, "Only worker accounts can be assigned a department")
    if payload.department not in DEPARTMENTS:
        raise HTTPException(422, "Choose a valid department for a worker")
    db.users.update_one({"user_id": user_id}, {"$set": {"departments": [payload.department]}})
    return public_user(db.users.find_one({"user_id": user_id}))

@app.get("/api/notifications")
def notifications(user=Depends(get_current_user)):
    return [serialize(item) for item in db.notifications.find({"user_id": user["user_id"]}).sort("created_at", -1).limit(50)]

@app.get("/api/analytics/summary")
def analytics(user=Depends(get_current_user)):
    require_admin(user)
    items = list(db.reports.find({}, {
        "_id": 0,
        "status": 1,
        "priority": 1,
        "department": 1,
        "category": 1,
        "ai_category": 1,
        "created_at": 1,
        "assigned_staff": 1,
        "status_history": 1,
    }))
    today = datetime.now(timezone.utc).date()
    daily = {
        (today - timedelta(days=offset)).isoformat(): {"date": (today - timedelta(days=offset)).isoformat(), "reports": 0}
        for offset in range(6, -1, -1)
    }
    categories = {}
    statuses = {}
    departments = {
        name: {"name": name, "total": 0, "open": 0, "in_progress": 0, "resolved": 0, "resolution_hours": []}
        for name in sorted(DEPARTMENTS | {"Unassigned"})
    }
    response_hours = []
    resolution_hours = []
    for report in items:
        status = report.get("status", "Unknown")
        categories[report.get("ai_category") or report.get("category", "Other")] = categories.get(report.get("ai_category") or report.get("category", "Other"), 0) + 1
        statuses[status] = statuses.get(status, 0) + 1
        created_at = report.get("created_at")
        if isinstance(created_at, datetime):
            date_key = created_at.date().isoformat()
            if date_key in daily:
                daily[date_key]["reports"] += 1
        department_name = report.get("department") or "Unassigned"
        department = departments.setdefault(
            department_name,
            {"name": department_name, "total": 0, "open": 0, "in_progress": 0, "resolved": 0, "resolution_hours": []},
        )
        department["total"] += 1
        if status in {"Resolved", "Closed"}:
            department["resolved"] += 1
        else:
            department["open"] += 1
        if status == "In progress":
            department["in_progress"] += 1
        if not isinstance(created_at, datetime):
            continue
        history = report.get("status_history", [])
        first_response = next(
            (entry.get("at") for entry in history if entry.get("status") not in {"Submitted"} and isinstance(entry.get("at"), datetime)),
            None,
        )
        resolved_at = next(
            (entry.get("at") for entry in history if entry.get("status") in {"Resolved", "Closed"} and isinstance(entry.get("at"), datetime)),
            None,
        )
        response_duration = _elapsed_hours(created_at, first_response)
        resolution_duration = _elapsed_hours(created_at, resolved_at)
        if response_duration is not None:
            response_hours.append(response_duration)
        if resolution_duration is not None:
            resolution_hours.append(resolution_duration)
            if department:
                department["resolution_hours"].append(resolution_duration)
    for department in departments.values():
        durations = department.pop("resolution_hours")
        department["average_resolution_hours"] = round(sum(durations) / len(durations), 1) if durations else None
    response_average = round(sum(response_hours) / len(response_hours), 1) if response_hours else None
    resolution_average = round(sum(resolution_hours) / len(resolution_hours), 1) if resolution_hours else None
    resolved_count = sum(statuses.get(status, 0) for status in ("Resolved", "Closed"))
    return {
        "total": len(items),
        "pending": sum(report.get("status") not in {"Resolved", "Closed"} for report in items),
        "high_priority": sum(report.get("priority") == "High" for report in items),
        "resolved": resolved_count,
        "in_progress": statuses.get("In progress", 0),
        "awaiting_assignment": sum(not report.get("assigned_staff") and report.get("status") not in {"Resolved", "Closed"} for report in items),
        "average_response_hours": response_average,
        "average_resolution_hours": resolution_average,
        "daily": list(daily.values()),
        "by_category": [{"name": name, "count": count} for name, count in sorted(categories.items(), key=lambda item: item[1], reverse=True)],
        "by_status": statuses,
        "departments": list(departments.values()),
    }

def _counts(items, key):
    result = {}
    for item in items: result[item.get(key, "Unassigned")] = result.get(item.get(key, "Unassigned"), 0) + 1
    return result

def _elapsed_hours(start, end):
    if not isinstance(end, datetime):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, (end - start).total_seconds() / 3600)
