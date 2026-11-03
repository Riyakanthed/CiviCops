import asyncio
import os
import unittest
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from fastapi import HTTPException
from pymongo.errors import ConfigurationError, DuplicateKeyError
from starlette.requests import Request

from app import auth, main


class FakeCollection:
    def __init__(self, documents=()):
        self.documents = list(documents)
        self.inserted = []

    @staticmethod
    def matches(item, query):
        for key, value in query.items():
            actual = item
            for part in key.split("."):
                actual = actual.get(part) if isinstance(actual, dict) else None
            if isinstance(value, dict):
                if "$exists" in value and ((key in item) != value["$exists"]):
                    return False
                if "$gt" in value and not (actual is not None and actual > value["$gt"]):
                    return False
                if "$ne" in value and actual == value["$ne"]:
                    return False
            elif actual != value:
                return False
        return True

    class Cursor:
        def __init__(self, documents):
            self.documents = documents

        def sort(self, key, direction):
            self.documents.sort(key=lambda item: item.get(key), reverse=direction < 0)
            return self

        def limit(self, count):
            self.documents = self.documents[:count]
            return self

        def __iter__(self):
            return iter(self.documents)

    def find(self, query, projection=None):
        return self.Cursor([item for item in self.documents if self.matches(item, query)])

    def find_one(self, query, projection=None):
        return next((item for item in self.documents if self.matches(item, query)), None)

    def aggregate(self, pipeline):
        report_ids = set(pipeline[0]["$match"]["report_id"]["$in"])
        user_id = pipeline[1]["$group"]["upvoted"]["$max"]["$cond"][0]["$eq"][1]
        summaries = {}
        for item in self.documents:
            if item.get("report_id") not in report_ids:
                continue
            summary = summaries.setdefault(item["report_id"], {"_id": item["report_id"], "count": 0, "upvoted": 0})
            summary["count"] += 1
            summary["upvoted"] = max(summary["upvoted"], int(item.get("user_id") == user_id))
        return list(summaries.values())

    def find_one_and_update(self, query, update, return_document=None):
        item = self.find_one(query)
        if item is None:
            return None
        item.update(update.get("$set", {}))
        return item

    def update_one(self, query, update, upsert=False):
        item = self.find_one(query)
        if item is None and upsert:
            item = dict(query)
            item.update(update.get("$setOnInsert", {}))
            self.documents.append(item)
        if item is not None:
            item.update(update.get("$set", {}))
            for key in update.get("$unset", {}):
                item.pop(key, None)
            for key, value in update.get("$push", {}).items():
                item.setdefault(key, []).append(value)

    def insert_one(self, document):
        if "report_id" in document and "user_id" in document and any(
            item.get("report_id") == document["report_id"] and item.get("user_id") == document["user_id"]
            for item in self.documents
        ):
            raise DuplicateKeyError("Duplicate report vote")
        self.inserted.append(document)
        self.documents.append(document)

    def count_documents(self, query):
        return sum(self.matches(item, query) for item in self.documents)


def fake_database(users=(), invitations=(), reports=(), notifications=()):
    return SimpleNamespace(
        users=FakeCollection(users),
        invitations=FakeCollection(invitations),
        reports=FakeCollection(reports),
        notifications=FakeCollection(notifications),
        report_votes=FakeCollection(),
        fs=SimpleNamespace(files=FakeCollection()),
    )


class AuthTokenTests(unittest.TestCase):
    def test_password_hash_is_salted_and_verified(self):
        first = auth.hash_password("correct horse battery")
        second = auth.hash_password("correct horse battery")
        self.assertNotEqual(first, second)
        self.assertTrue(auth.verify_password("correct horse battery", first))
        self.assertFalse(auth.verify_password("wrong password", first))

    def test_session_token_round_trip_and_previous_session_rejection(self):
        with patch.dict(os.environ, {"JWT_SECRET": "x" * 48}):
            token = auth.create_session_token("account-id")
            self.assertEqual(auth.decode_session_token(token), "account-id")
            old_token = jwt.encode({"sub": "account-id"}, "x" * 48, algorithm="HS256")
            with self.assertRaises(HTTPException) as error:
                auth.decode_session_token(old_token)
        self.assertEqual(error.exception.status_code, 401)

    def test_short_session_secret_is_rejected(self):
        with patch.dict(os.environ, {"JWT_SECRET": "too-short"}):
            with self.assertRaises(HTTPException) as error:
                auth.create_session_token("account-id")
        self.assertEqual(error.exception.status_code, 503)

    def test_invitation_codes_are_stored_as_hashes(self):
        code = auth.create_invitation_code()
        self.assertNotEqual(auth.hash_invitation_code(code), code)
        self.assertEqual(len(auth.hash_invitation_code(code)), 64)


class ApiAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.original_db = main.db
        self.original_client = main.client
        self.original_database_ready = main.database_ready
        self.env = patch.dict(os.environ, {
            "JWT_SECRET": "s" * 48,
            "BOOTSTRAP_ADMIN_TOKEN": "bootstrap-token-for-local-first-admin",
            "AUTH_ADMIN_DOMAIN": "admin.example.org",
            "AUTH_WORKER_DOMAIN": "staff.example.org",
            "AUTH_CITIZEN_DOMAIN": "example.org",
        })
        self.env.start()

    def tearDown(self):
        main.db = self.original_db
        main.client = self.original_client
        main.database_ready = self.original_database_ready
        self.env.stop()

    def test_mongodb_client_is_initialized_only_when_requested(self):
        main.db = None
        main.client = None
        main.database_ready = False
        with patch.object(main, "MongoClient", side_effect=ConfigurationError("DNS resolution timed out")):
            with self.assertRaises(ConfigurationError):
                main.initialize_database()
        self.assertIsNone(main.client)
        self.assertIsNone(main.db)

    def test_api_returns_clear_service_unavailable_while_mongodb_dns_is_down(self):
        main.db = None
        main.client = None
        main.database_ready = False

        async def call_next(_request):
            self.fail("Request should not reach the endpoint without MongoDB")

        request = Request({
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [],
        })
        with patch.object(main, "MongoClient", side_effect=ConfigurationError("DNS resolution timed out")):
            response = asyncio.run(main.require_database(request, call_next))

        self.assertEqual(response.status_code, 503)
        self.assertFalse(main.database_ready)

    def test_health_reports_degraded_when_srv_dns_fails(self):
        main.db = None
        main.client = None
        main.database_ready = False
        with patch.object(main, "MongoClient", side_effect=ConfigurationError("DNS resolution timed out")):
            result = main.health()
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["database"], "unavailable")

    def test_external_identity_route_is_removed(self):
        paths = {route.path for route in main.app.routes}
        auth_routes = {path for path in paths if path.startswith("/api/auth/")}
        self.assertEqual(auth_routes, {"/api/auth/login", "/api/auth/register", "/api/auth/me"})
        self.assertIn("/api/auth/login", paths)
        self.assertIn("/api/auth/register", paths)

    def test_legacy_role_headers_do_not_authenticate_requests(self):
        with self.assertRaises(HTTPException) as error:
            main.get_current_user(None)
        self.assertEqual(error.exception.status_code, 401)

    def test_community_feed_shows_other_citizens_reports_without_private_fields(self):
        main.db = fake_database(reports=[
            {
                "report_id": "CC-2026-OTHER",
                "citizen_id": "other-citizen",
                "citizen_email": "other@example.org",
                "title": "Blocked drain",
                "description": "Water is pooling near the crossing",
                "category": "Drainage",
                "ai_category": "Drainage",
                "status": "In progress",
                "priority": "Medium",
                "location": {"latitude": 40.7, "longitude": -73.9, "address": "Pine Street"},
                "assigned_staff": "worker-id",
                "status_history": [{"status": "Submitted", "at": "then", "by": "citizen-id", "note": "Private note"}],
            },
            {"report_id": "CC-2026-OWN", "citizen_id": "citizen-id", "title": "My report"},
        ])

        result = main.list_community_reports(user={"user_id": "citizen-id", "role": "Citizen"}, limit=100)

        self.assertEqual([report["report_id"] for report in result], ["CC-2026-OTHER"])
        self.assertEqual(result[0]["status_history"], [{"status": "Submitted", "at": "then"}])
        self.assertNotIn("citizen_id", result[0])
        self.assertNotIn("citizen_email", result[0])
        self.assertNotIn("assigned_staff", result[0])
        self.assertNotIn("by", result[0]["status_history"][0])
        self.assertNotIn("note", result[0]["status_history"][0])

    def test_citizen_can_view_other_report_with_public_fields_only(self):
        report = {
            "report_id": "CC-2026-OTHER",
            "citizen_id": "other-citizen",
            "citizen_email": "other@example.org",
            "title": "Blocked drain",
            "description": "Water is pooling near the crossing",
            "status": "Resolved",
            "status_history": [{"status": "Resolved", "at": "then", "by": "worker-id"}],
        }
        main.db = fake_database(reports=[report])

        result = main.get_report("CC-2026-OTHER", user={"user_id": "citizen-id", "role": "Citizen"})

        self.assertEqual(result["status"], "Resolved")
        self.assertNotIn("citizen_email", result)
        self.assertNotIn("citizen_id", result)
        self.assertNotIn("by", result["status_history"][0])

    def test_community_upvotes_are_persistent_unique_and_increase_priority(self):
        main.db = fake_database(reports=[{
            "report_id": "CC-2026-UPVOTE",
            "citizen_id": "report-owner",
            "status": "Submitted",
            "priority": "Medium",
            "priority_score": 68,
        }])
        voter = {"user_id": "citizen-id", "role": "Citizen"}

        first = main.upvote_community_report("CC-2026-UPVOTE", voter)
        duplicate = main.upvote_community_report("CC-2026-UPVOTE", voter)

        self.assertEqual(len(main.db.report_votes.documents), 1)
        self.assertEqual(first["upvotes"], 1)
        self.assertEqual(first["priority"], "Medium")
        self.assertEqual(duplicate["upvotes"], 1)
        self.assertEqual(duplicate["priority_score"], 69)

        second = main.upvote_community_report(
            "CC-2026-UPVOTE",
            {"user_id": "another-citizen", "role": "Citizen"},
        )
        self.assertEqual(second["upvotes"], 2)
        self.assertEqual(second["priority"], "High")

    def test_citizens_cannot_upvote_their_own_or_resolved_reports(self):
        main.db = fake_database(reports=[
            {"report_id": "CC-2026-OWN", "citizen_id": "citizen-id", "status": "Submitted"},
            {"report_id": "CC-2026-DONE", "citizen_id": "other-id", "status": "Resolved"},
        ])
        voter = {"user_id": "citizen-id", "role": "Citizen"}

        with self.assertRaises(HTTPException) as own_error:
            main.upvote_community_report("CC-2026-OWN", voter)
        with self.assertRaises(HTTPException) as resolved_error:
            main.upvote_community_report("CC-2026-DONE", voter)

        self.assertEqual(own_error.exception.status_code, 403)
        self.assertEqual(resolved_error.exception.status_code, 409)

    def test_community_feed_shows_vote_count_and_voter_state(self):
        main.db = fake_database(reports=[{
            "report_id": "CC-2026-FEED",
            "citizen_id": "report-owner",
            "title": "Blocked drain",
            "status": "Submitted",
            "priority": "Medium",
            "priority_score": 69,
        }])
        voter = {"user_id": "citizen-id", "role": "Citizen"}
        main.upvote_community_report("CC-2026-FEED", voter)

        report = main.list_community_reports(user=voter, limit=100)[0]

        self.assertEqual(report["upvotes"], 1)
        self.assertTrue(report["upvoted"])
        self.assertEqual(report["priority"], "High")

    def test_admin_report_list_prioritizes_active_upvoted_reports(self):
        main.db = fake_database(reports=[
            {"_id": "base", "report_id": "CC-2026-BASE", "status": "Submitted", "priority": "High", "priority_score": 70, "created_at": main.datetime.now(main.timezone.utc)},
            {"_id": "popular", "report_id": "CC-2026-POPULAR", "status": "Submitted", "priority": "Medium", "priority_score": 69, "created_at": main.datetime.now(main.timezone.utc)},
            {"_id": "resolved", "report_id": "CC-2026-RESOLVED", "status": "Resolved", "priority": "High", "priority_score": 100, "created_at": main.datetime.now(main.timezone.utc)},
        ])
        main.db.report_votes.insert_one({"report_id": "CC-2026-POPULAR", "user_id": "citizen-a"})
        main.db.report_votes.insert_one({"report_id": "CC-2026-POPULAR", "user_id": "citizen-b"})

        reports = main.list_reports(user={"user_id": "admin-id", "role": "Admin"}, limit=100)

        self.assertEqual(
            [report["report_id"] for report in reports],
            ["CC-2026-POPULAR", "CC-2026-BASE", "CC-2026-RESOLVED"],
        )
        self.assertEqual(reports[0]["priority"], "High")
        self.assertEqual(reports[0]["upvotes"], 2)

    def test_admin_analytics_are_calculated_from_saved_reports(self):
        created = main.datetime.now(main.timezone.utc) - main.timedelta(days=1)
        responded = created + main.timedelta(hours=2)
        resolved = created + main.timedelta(hours=26)
        main.db = fake_database(reports=[
            {
                "status": "Resolved",
                "priority": "High",
                "department": "Public Works",
                "category": "Road damage",
                "ai_category": "Road damage",
                "assigned_staff": "worker-id",
                "created_at": created,
                "status_history": [
                    {"status": "Submitted", "at": created},
                    {"status": "Assigned", "at": responded},
                    {"status": "Resolved", "at": resolved},
                ],
            },
            {
                "status": "In progress",
                "priority": "Medium",
                "department": "Public Works",
                "category": "Road damage",
                "created_at": created,
                "status_history": [{"status": "Submitted", "at": created}],
            },
        ])

        result = main.analytics(user={"user_id": "admin-id", "role": "Admin"})

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["high_priority"], 1)
        self.assertEqual(result["average_response_hours"], 2)
        self.assertEqual(result["average_resolution_hours"], 26)
        public_works = next(item for item in result["departments"] if item["name"] == "Public Works")
        self.assertEqual(public_works["open"], 1)
        self.assertEqual(public_works["resolved"], 1)
        self.assertEqual(public_works["average_resolution_hours"], 26)

    def test_media_is_hidden_from_citizens_who_do_not_own_the_report(self):
        media_id = main.ObjectId()
        main.db = fake_database(reports=[{
            "report_id": "CC-2026-OWN",
            "citizen_id": "owner-id",
            "assigned_staff": "worker-id",
            "media": [{"media_id": str(media_id)}],
        }])
        main.db.fs.files.insert_one({
            "_id": media_id,
            "metadata": {"report_id": "CC-2026-OWN", "content_type": "image/png"},
        })

        with self.assertRaises(HTTPException) as error:
            main.get_report_media(str(media_id), user={"user_id": "other-id", "role": "Citizen"})

        self.assertEqual(error.exception.status_code, 404)

    def test_report_image_upload_is_saved_to_gridfs(self):
        main.db = fake_database()
        media_id = main.ObjectId()
        image = SimpleNamespace(
            content_type="image/png",
            filename="crossing.png",
            file=BytesIO(b"image-bytes"),
        )
        with patch.object(main, "GridFSBucket") as bucket:
            bucket.return_value.upload_from_stream.return_value = media_id

            result = main.upload_media(image, user={"user_id": "citizen-id", "role": "Citizen"})

        self.assertEqual(result["media_id"], str(media_id))
        self.assertEqual(result["content_type"], "image/png")
        self.assertEqual(result["size"], len(b"image-bytes"))
        bucket.return_value.upload_from_stream.assert_called_once()

    def test_admin_and_assigned_worker_can_download_report_image(self):
        media_id = main.ObjectId()
        main.db = fake_database(reports=[{
            "report_id": "CC-2026-IMAGE",
            "citizen_id": "citizen-id",
            "assigned_staff": "worker-id",
        }])
        main.db.fs.files.insert_one({
            "_id": media_id,
            "metadata": {"report_id": "CC-2026-IMAGE", "content_type": "image/png"},
        })
        with patch.object(main, "GridFSBucket") as bucket:
            bucket.return_value.open_download_stream.side_effect = lambda _: BytesIO(b"image-bytes")
            for user in (
                {"user_id": "admin-id", "role": "Admin"},
                {"user_id": "worker-id", "role": "Worker"},
            ):
                response = main.get_report_media(str(media_id), user=user)
                self.assertEqual(response.media_type, "image/png")
                self.assertEqual(response.headers["cache-control"], "private, no-store")
                self.assertEqual(
                    asyncio.run(self.read_stream(response.body_iterator)),
                    b"image-bytes",
                )

    @staticmethod
    async def read_stream(stream):
        return b"".join([chunk async for chunk in stream])

    def test_exact_email_domain_assigns_citizen_role(self):
        self.assertEqual(main.role_for_email("person@example.org"), "Citizen")
        self.assertIsNone(main.role_for_email("person@sub.example.org"))

    def test_new_citizen_registration_hashes_password_and_returns_jwt(self):
        main.db = fake_database()
        result = main.register(main.AuthRegistration(
            name="New Citizen",
            email="Citizen@Example.org",
            password="long-enough-password",
        ))
        account = main.db.users.find_one({"email": "citizen@example.org"})
        self.assertEqual(result["user"]["role"], "Citizen")
        self.assertEqual(auth.decode_session_token(result["access_token"]), account["user_id"])
        self.assertNotEqual(account["password_hash"], "long-enough-password")

    def test_admin_user_list_reads_all_registered_roles_without_password_hashes(self):
        main.db = fake_database(users=[
            {"user_id": "citizen", "email": "citizen@example.org", "name": "Citizen", "role": "Citizen", "password_hash": "secret"},
            {"user_id": "worker", "email": "worker@staff.example.org", "name": "Worker", "role": "Worker", "password_hash": "secret"},
            {"user_id": "admin", "email": "admin@admin.example.org", "name": "Admin", "role": "Admin", "password_hash": "secret"},
        ])

        users = main.admin_users(user={"user_id": "admin", "role": "Admin"})

        self.assertEqual({user["role"] for user in users}, {"Citizen", "Worker", "Admin"})
        self.assertNotIn("password_hash", users[0])

    def test_admin_registration_requires_bootstrap_code_once(self):
        main.db = fake_database()
        result = main.register(main.AuthRegistration(
            name="First Admin",
            email="first@admin.example.org",
            password="long-enough-password",
            access_code="bootstrap-token-for-local-first-admin",
        ))
        self.assertEqual(result["user"]["role"], "Admin")
        with self.assertRaises(HTTPException) as error:
            main.register(main.AuthRegistration(
                name="Another Admin",
                email="another@admin.example.org",
                password="long-enough-password",
                access_code="bootstrap-token-for-local-first-admin",
            ))
        self.assertEqual(error.exception.status_code, 403)

    def test_staff_registration_requires_matching_one_time_invitation(self):
        main.db = fake_database()
        with self.assertRaises(HTTPException) as error:
            main.register(main.AuthRegistration(
                name="Worker",
                email="worker@staff.example.org",
                password="long-enough-password",
            ))
        self.assertEqual(error.exception.status_code, 403)

        admin = {"user_id": "admin-id", "role": "Admin"}
        invitation = main.create_invitation(main.InvitationCreate(
            email="worker@staff.example.org",
            role="Worker",
            department="Public Works",
        ), admin)
        result = main.register(main.AuthRegistration(
            name="Worker",
            email="worker@staff.example.org",
            password="long-enough-password",
            access_code=invitation["code"],
        ))
        self.assertEqual(result["user"]["role"], "Worker")
        self.assertEqual(result["user"]["departments"], ["Public Works"])
        with self.assertRaises(HTTPException) as reused:
            main.register(main.AuthRegistration(
                name="Second Worker",
                email="worker@staff.example.org",
                password="long-enough-password",
                access_code=invitation["code"],
            ))
        self.assertEqual(reused.exception.status_code, 409)

    def test_invitation_role_must_match_email_domain(self):
        main.db = fake_database()
        with self.assertRaises(HTTPException) as error:
            main.create_invitation(
                main.InvitationCreate(email="citizen@example.org", role="Admin"),
                {"user_id": "admin-id", "role": "Admin"},
            )
        self.assertEqual(error.exception.status_code, 422)

    def test_legacy_account_requires_invitation_to_set_password(self):
        main.db = fake_database(users=[{
            "_id": "mongo-user",
            "user_id": "legacy-user",
            "email": "legacy@example.org",
            "name": "Legacy Citizen",
            "role": "Citizen",
            "departments": [],
        }])
        with self.assertRaises(HTTPException) as error:
            main.register(main.AuthRegistration(
                name="Legacy Citizen",
                email="legacy@example.org",
                password="long-enough-password",
            ))
        self.assertEqual(error.exception.status_code, 403)

    def test_existing_citizen_outside_configured_domains_can_be_migrated_with_invite(self):
        main.db = fake_database(users=[{
            "_id": "legacy-mongo-user",
            "user_id": "legacy-citizen",
            "email": "legacy@gmail.com",
            "name": "Legacy Citizen",
            "role": "Citizen",
            "departments": [],
        }])
        invitation = main.create_invitation(
            main.InvitationCreate(email="legacy@gmail.com", role="Citizen"),
            {"user_id": "admin-id", "role": "Admin"},
        )
        result = main.register(main.AuthRegistration(
            name="Legacy Citizen",
            email="legacy@gmail.com",
            password="long-enough-password",
            access_code=invitation["code"],
        ))
        self.assertEqual(result["user"]["user_id"], "legacy-citizen")
        self.assertEqual(result["user"]["role"], "Citizen")

    def test_login_rejects_bad_password_and_accepts_correct_password(self):
        password_hash = auth.hash_password("long-enough-password")
        main.db = fake_database(users=[{
            "user_id": "citizen-id",
            "email": "citizen@example.org",
            "name": "Citizen",
            "role": "Citizen",
            "departments": [],
            "password_hash": password_hash,
        }])
        with self.assertRaises(HTTPException) as error:
            main.login(main.AuthLogin(email="citizen@example.org", password="incorrect"))
        self.assertEqual(error.exception.status_code, 401)
        result = main.login(main.AuthLogin(email="citizen@example.org", password="long-enough-password"))
        self.assertEqual(result["user"]["user_id"], "citizen-id")

    def test_citizen_cannot_manage_staff_accounts(self):
        with self.assertRaises(HTTPException) as error:
            main.admin_users({"user_id": "citizen", "role": "Citizen"})
        self.assertEqual(error.exception.status_code, 403)

    def test_worker_cannot_update_an_unassigned_report(self):
        main.db = fake_database(reports=[{"report_id": "CC-1", "assigned_staff": "another-worker"}])
        with self.assertRaises(HTTPException) as error:
            main.update_status(
                "CC-1",
                main.StatusUpdate(status="In progress"),
                {"user_id": "worker", "role": "Worker"},
            )
        self.assertEqual(error.exception.status_code, 403)

    def test_admin_assignment_records_worker_and_notification(self):
        report = {"_id": "mongo-1", "report_id": "CC-1", "department": "Public Works", "citizen_id": "citizen", "title": "Road repair"}
        worker = {"user_id": "worker-1", "role": "Worker", "name": "Jordan Worker", "email": "worker@staff.example.org", "departments": ["Public Works"]}
        main.db = fake_database(reports=[report], users=[worker])
        updated = main.assign_report("CC-1", main.ReportAssignment(worker_id="worker-1"), {"user_id": "admin", "role": "Admin"})
        self.assertEqual(updated["assigned_staff"], "worker-1")
        self.assertEqual(updated["status"], "Assigned")
        self.assertEqual(len(main.db.notifications.inserted), 2)


if __name__ == "__main__":
    unittest.main()
