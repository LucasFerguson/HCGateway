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
        self.username = "test-auth-" + uuid.uuid4().hex[:16]
        self.password = "correct horse battery"
        self.client = self.app.test_client()

    def tearDown(self):
        user_ids = [u["_id"] for u in self.control["users"].find({"username": {"$regex": "^test-auth-"}})]
        self.control["sessions"].delete_many({"userId": {"$in": user_ids}})
        self.control["users"].delete_many({"_id": {"$in": user_ids}})
        self.control["account_requests"].delete_many({"username": {"$regex": "^test-auth-"}})
        self.control["login_failures"].delete_many({"username": {"$regex": "^test-auth-"}})
        self.mongo.close()

    def request_account(self, username=None, password=None):
        return self.client.post("/api/v2/signup", json={
            "username": username or self.username,
            "password": password or self.password,
        })

    def signup(self):
        """Request an account, approve it, and sign in, like a real new user."""
        from account_requests import approve_request

        self.assertEqual(self.request_account().status_code, 202)
        self.assertIsNotNone(approve_request(self.control, self.username))
        return self.login()

    def login(self):
        response = self.client.post("/api/v2/login", json={"username": self.username, "password": self.password})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def status(self, token):
        return self.client.get("/api/v2/sync/status", headers={"Authorization": "Bearer " + token}).status_code

    def test_login_with_unknown_username_does_not_create_an_account(self):
        response = self.client.post("/api/v2/login", json={"username": self.username, "password": self.password})
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.control["users"].find_one({"username": self.username}))

    def test_unknown_username_and_wrong_password_look_the_same(self):
        self.signup()
        wrong = self.client.post("/api/v2/login", json={"username": self.username, "password": "not it at all"})
        unknown = self.client.post("/api/v2/login", json={"username": self.username + "x", "password": "not it at all"})
        self.assertEqual(wrong.status_code, unknown.status_code)
        self.assertEqual(wrong.get_json(), unknown.get_json())

    def test_signup_only_queues_a_request(self):
        response = self.request_account()
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()["pending"])
        self.assertNotIn("token", response.get_json())
        self.assertIsNone(self.control["users"].find_one({"username": self.username}))
        stored = self.control["account_requests"].find_one({"username": self.username})
        self.assertNotIn(self.password, str(stored))

    def test_pending_account_cannot_sign_in_until_approved(self):
        from account_requests import approve_request

        self.request_account()
        waiting = self.client.post("/api/v2/login", json={"username": self.username, "password": self.password})
        self.assertEqual(waiting.status_code, 403)
        self.assertTrue(waiting.get_json()["pending"])
        guess = self.client.post("/api/v2/login", json={"username": self.username, "password": "a wrong guess"})
        self.assertNotIn("pending", guess.get_json())

        approve_request(self.control, self.username)
        self.login()
        self.assertIsNone(self.control["account_requests"].find_one({"username": self.username}))

    def test_rejected_request_is_gone(self):
        from account_requests import reject_request

        self.request_account()
        self.assertTrue(reject_request(self.control, self.username))
        self.assertIsNone(self.control["account_requests"].find_one({"username": self.username}))

    def test_signup_answers_the_same_for_taken_usernames(self):
        self.signup()
        taken = self.request_account(password="some other password")
        self.assertEqual(taken.status_code, 202)
        self.assertIsNone(self.control["account_requests"].find_one({"username": self.username}))
        short = self.request_account(username=self.username + "x", password="short")
        self.assertEqual(short.status_code, 400)

    def test_repeated_failures_lock_the_username(self):
        from login_throttle import MAX_FAILURES

        self.signup()
        for _ in range(MAX_FAILURES):
            self.client.post("/api/v2/login", json={"username": self.username, "password": "a wrong guess"})
        locked = self.client.post("/api/v2/login", json={"username": self.username, "password": self.password})
        self.assertEqual(locked.status_code, 429)

    def test_successful_login_clears_failures(self):
        self.signup()
        self.client.post("/api/v2/login", json={"username": self.username, "password": "a wrong guess"})
        self.login()
        self.assertEqual(self.control["login_failures"].count_documents({"username": self.username}), 0)

    def test_usernames_are_lowercased(self):
        self.username = self.username.upper()
        response = self.request_account()
        self.assertEqual(response.status_code, 202)
        self.username = self.username.lower()
        self.assertIsNotNone(self.control["account_requests"].find_one({"username": self.username}))

    def test_login_matches_any_case(self):
        self.signup()
        response = self.client.post("/api/v2/login", json={"username": self.username.upper(), "password": self.password})
        self.assertEqual(response.status_code, 201)

    def test_signup_allows_only_safe_username_characters(self):
        for bad in ["ab", "x" * 33, "has space", "dollar$sign", "{\"$ne\":1}", "esc\x1b[31m", "luc\u0430s", ".dot-first", "slash/name"]:
            response = self.request_account(username=bad)
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(self.control["account_requests"].count_documents({"username": {"$regex": "\\$|\\x1b|\\s"}}), 0)

    def test_overlong_password_is_refused(self):
        self.assertEqual(self.request_account(password="p" * 257).status_code, 400)
        response = self.client.post("/api/v2/login", json={"username": self.username, "password": "p" * 257})
        self.assertEqual(response.status_code, 403)

    def test_malformed_bodies_are_400_not_500(self):
        for endpoint in ("/api/v2/login", "/api/v2/signup", "/api/v2/refresh"):
            for body in ([], "text", 7, None):
                response = self.client.post(endpoint, json=body)
                self.assertEqual(response.status_code, 400, (endpoint, body))
        response = self.client.post("/api/v2/refresh", json={"refresh": {"$ne": None}})
        self.assertEqual(response.status_code, 400)

    def test_client_label_is_allowlisted(self):
        self.client.post("/api/v2/signup", json={"username": self.username, "password": self.password, "client": {"$gt": ""}})
        self.assertIsNone(self.control["account_requests"].find_one({"username": self.username})["client"])

    def test_login_rejects_query_operators(self):
        response = self.client.post("/api/v2/login", json={"username": {"$ne": None}, "password": "anything"})
        self.assertEqual(response.status_code, 400)

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
