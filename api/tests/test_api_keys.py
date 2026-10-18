import os
import unittest
import uuid

from pymongo import MongoClient


@unittest.skipUnless(os.environ.get("TEST_MONGO_URI"), "TEST_MONGO_URI is required")
class ApiKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from main import app

        cls.app = app

    def setUp(self):
        from account_requests import approve_request

        self.mongo = MongoClient(os.environ["TEST_MONGO_URI"])
        self.control = self.mongo["hcgateway"]
        self.client = self.app.test_client()
        self.username = "test-keys-" + uuid.uuid4().hex[:16]
        self.password = "correct horse battery"
        self.client.post("/api/v2/signup", json={"username": self.username, "password": self.password})
        approve_request(self.control, self.username)
        self.user_id = self.control["users"].find_one({"username": self.username})["_id"]
        self.auth = {"Authorization": "Bearer " + self.login()}

    def tearDown(self):
        user_ids = [u["_id"] for u in self.control["users"].find({"username": {"$regex": "^test-keys-"}})]
        self.control["api_keys"].delete_many({"userId": {"$in": user_ids}})
        self.control["sessions"].delete_many({"userId": {"$in": user_ids}})
        self.control["users"].delete_many({"_id": {"$in": user_ids}})
        self.control["account_requests"].delete_many({"username": {"$regex": "^test-keys-"}})
        self.mongo.close()

    def login(self):
        response = self.client.post("/api/v2/login", json={"username": self.username, "password": self.password})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["token"]

    def create(self, name="laptop export", auth=None):
        return self.client.post("/api/v2/api-keys", headers=auth or self.auth, json={"name": name})

    def test_create_shows_the_key_once_and_stores_only_its_hash(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertTrue(body["key"].startswith("hcg_"))
        self.assertTrue(body["key"].startswith(body["prefix"]))
        stored = self.control["api_keys"].find_one({"_id": body["id"]})
        self.assertNotIn(body["key"], str(stored))
        self.assertEqual(stored["scope"], "read")

        listed = self.client.get("/api/v2/api-keys", headers=self.auth).get_json()["keys"]
        self.assertEqual([k["id"] for k in listed], [body["id"]])
        self.assertNotIn("key", listed[0])
        self.assertNotIn("keyHash", listed[0])

    def test_a_key_is_not_accepted_by_the_rest_api(self):
        key = self.create().get_json()["key"]
        as_key = {"Authorization": "Bearer " + key}
        self.assertEqual(self.client.get("/api/v2/account", headers=as_key).status_code, 401)
        self.assertEqual(self.client.get("/api/v2/api-keys", headers=as_key).status_code, 401)
        self.assertEqual(self.create(auth=as_key).status_code, 401)
        self.assertEqual(self.client.post("/api/v2/sync/steps", headers=as_key, json={"data": []}).status_code, 401)

    def test_revoke_only_your_own_keys(self):
        mine = self.create().get_json()["id"]
        other = self.control["api_keys"].insert_one({
            "_id": "0" * 24, "userId": "someone-else", "name": "theirs",
            "keyHash": "x" + uuid.uuid4().hex, "prefix": "hcg_xxxxxx",
        })
        try:
            self.assertEqual(self.client.delete("/api/v2/api-keys/" + "0" * 24, headers=self.auth).status_code, 404)
            self.assertIsNotNone(self.control["api_keys"].find_one({"_id": "0" * 24}))
            self.assertEqual(self.client.delete("/api/v2/api-keys/" + mine, headers=self.auth).status_code, 200)
            self.assertIsNone(self.control["api_keys"].find_one({"_id": mine}))
            self.assertEqual(self.client.delete("/api/v2/api-keys/not-an-id", headers=self.auth).status_code, 404)
        finally:
            self.control["api_keys"].delete_one({"_id": other.inserted_id})

    def test_names_are_allowlisted(self):
        for bad in ["", "   ", "x" * 49, "<script>", {"$ne": 1}, "tab\tname\x1b"]:
            self.assertEqual(self.create(name=bad).status_code, 400, bad)
        self.assertEqual(self.create(name="  grafana   (home)  ").get_json()["name"], "grafana (home)")

    def test_key_limit(self):
        from api_keys import MAX_KEYS_PER_USER

        for i in range(MAX_KEYS_PER_USER):
            self.assertEqual(self.create(name=f"key {i}").status_code, 201)
        self.assertEqual(self.create(name="one too many").status_code, 409)

    def test_needs_a_session(self):
        self.assertEqual(self.client.get("/api/v2/api-keys").status_code, 401)


if __name__ == "__main__":
    unittest.main()
