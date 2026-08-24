import argparse
import logging
import os
import signal
import socket
import time
import uuid

from dotenv import load_dotenv
from pymongo import MongoClient

from .jobs import claim_job, complete_job, enqueue_missing_users, fail_job
from .service import process_user


load_dotenv()
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("analytics-worker")
stopping = False


def request_stop(*_):
    global stopping
    stopping = True


def process_one(mongo, worker_id):
    control = mongo["hcgateway"]
    job = claim_job(control, worker_id, int(os.environ.get("ANALYTICS_LEASE_SECONDS", "900")))
    if not job:
        return False
    try:
        user = control["users"].find_one({"_id": str(job["_id"])})
        if not user:
            raise ValueError(f"analytics user {job['_id']} does not exist")
        logger.info("processing analytics user=%s revision=%s", job["_id"], job.get("requestedRevision"))
        result = process_user(mongo, user)
        complete_job(control, job, result)
        logger.info("analytics completed user=%s persistence=%s", job["_id"], result["persistence"])
    except Exception as error:
        logger.exception("analytics failed user=%s", job["_id"])
        fail_job(control, job, error)
    return True


def run(drain=False):
    mongo = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=10_000)
    try:
        mongo.admin.command("ping")
        control = mongo["hcgateway"]
        enqueue_missing_users(control, control["users"].find({}, {"_id": 1}))
        worker_id = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        poll_seconds = max(1, int(os.environ.get("ANALYTICS_POLL_SECONDS", "15")))
        while not stopping:
            worked = process_one(mongo, worker_id)
            if drain and not worked:
                break
            if not worked:
                time.sleep(poll_seconds)
    finally:
        mongo.close()


def main():
    parser = argparse.ArgumentParser(description="HCGateway analytics background worker")
    parser.add_argument("--drain", action="store_true", help="process queued jobs and exit")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    run(drain=args.drain)


if __name__ == "__main__":
    main()
