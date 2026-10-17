import datetime as dt
import os
import unittest
import uuid

from pymongo import MongoClient


@unittest.skipUnless(os.environ.get("TEST_MONGO_URI"), "TEST_MONGO_URI is required")
class AuthSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import app

        cls.app = app

    def setUp(self):
        self.mongo = MongoClient(os.environ["TEST_MONGO_URI"])
        self.control = self.mongo["hcgateway"]
        self.username = "test-auth-" + uuid.uuid4().hex
        self.password = "correct horse battery"
        self.client = self.app.test_client()

    def tearDown(self):
        user_ids = [u["_id"] for u in self.control["users"].find({"username": {"$regex": "^test-auth-"}})]
        self.control["sessions"].delete_many({"userId": {"$in": user_ids}})
        self.control["users"].delete_many({"_id": {"$in": user_ids}})
        self.mongo.close()

    def signup(self):
        response = self.client.post("/api/v2/signup", json={"username": self.username, "password": self.password})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def login(self):
        response = self.client.post("/api/v2/login", json={"username": self.username, "password": self.password})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def status(self, token):
        return self.client.get("/api/v2/sync/status", headers={"Authorization": "Bearer " + token}).status_code

    def test_login_with_unknown_username_does_not_create_an_account(self):
        response = self.client.post("/api/v2/login", json={"username": self.username, "password": self.password})
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(self.control["users"].find_one({"username": self.username}))

    def test_signup_rejects_taken_username_and_short_password(self):
        self.signup()
        taken = self.client.post("/api/v2/signup", json={"username": self.username, "password": self.password})
        self.assertEqual(taken.status_code, 409)
        short = self.client.post("/api/v2/signup", json={"username": self.username + "x", "password": "short"})
        self.assertEqual(short.status_code, 400)

    def test_each_login_is_an_independent_session(self):
        self.signup()
        phone = self.login()
        browser = self.login()
        self.assertNotEqual(phone["token"], browser["token"])
        self.assertEqual(self.status(phone["token"]), 200)
        self.assertEqual(self.status(browser["token"]), 200)

        revoked = self.client.delete("/api/v2/revoke", headers={"Authorization": "Bearer " + browser["token"]})
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.status(browser["token"]), 401)
        self.assertEqual(self.status(phone["token"]), 200)

    def test_tokens_are_stored_hashed(self):
        session = self.signup()
        stored = self.control["sessions"].find_one({"userId": self.control["users"].find_one({"username": self.username})["_id"]})
        self.assertNotIn(session["token"], str(stored))
        self.assertNotIn(session["refresh"], str(stored))

    def test_refresh_replaces_only_that_sessions_token(self):
        self.signup()
        phone = self.login()
        browser = self.login()
        response = self.client.post("/api/v2/refresh", json={"refresh": phone["refresh"]})
        self.assertEqual(response.status_code, 200)
        refreshed = response.get_json()
        self.assertEqual(self.status(refreshed["token"]), 200)
        self.assertEqual(self.status(phone["token"]), 401)
        self.assertEqual(self.status(browser["token"]), 200)

    def test_legacy_refresh_token_migrates_to_a_session(self):
        user_id = "test-auth-legacy-" + uuid.uuid4().hex
        self.control["users"].insert_one({
            "_id": user_id,
            "username": "test-auth-" + user_id,
            "password": "$argon2id$test-hash",
            "token": "legacy-token-" + user_id,
            "refresh": "legacy-refresh-" + user_id,
            "expiry": dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1),
        })
        self.assertEqual(self.status("legacy-token-" + user_id), 401)

        response = self.client.post("/api/v2/refresh", json={"refresh": "legacy-refresh-" + user_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.status(response.get_json()["token"]), 200)
        self.assertNotIn("refresh", self.control["users"].find_one({"_id": user_id}))

        again = self.client.post("/api/v2/refresh", json={"refresh": response.get_json()["refresh"]})
        self.assertEqual(again.status_code, 200)


    def test_change_password(self):
        session = self.signup()
        phone = self.login()
        auth = {"Authorization": "Bearer " + session["token"]}
        self.assertEqual(self.client.get("/api/v2/account", headers=auth).get_json(), {"username": self.username})

        wrong = self.client.post("/api/v2/account/password", headers=auth, json={"currentPassword": "nope", "newPassword": "brand new pass"})
        self.assertEqual(wrong.status_code, 403)
        short = self.client.post("/api/v2/account/password", headers=auth, json={"currentPassword": self.password, "newPassword": "short"})
        self.assertEqual(short.status_code, 400)
        ok = self.client.post("/api/v2/account/password", headers=auth, json={"currentPassword": self.password, "newPassword": "brand new pass"})
        self.assertEqual(ok.status_code, 200)

        old = self.client.post("/api/v2/login", json={"username": self.username, "password": self.password})
        self.assertEqual(old.status_code, 403)
        self.password = "brand new pass"
        self.login()
        self.assertEqual(self.status(phone["token"]), 200)

if __name__ == "__main__":
    unittest.main()
