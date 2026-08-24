from flask import Blueprint, request, jsonify, g
import os, json
from dotenv import load_dotenv
load_dotenv()
from pyfcm import FCMNotification

import pymongo
from bson.objectid import ObjectId
from bson.errors import *
mongo = pymongo.MongoClient(os.environ['MONGO_URI'])

from argon2 import PasswordHasher
ph = PasswordHasher()

from cryptography.fernet import Fernet
import base64, secrets, datetime
from zoneinfo import ZoneInfo

from analytics_engine.context import AnalyticsContext, context_for_user, validate_context
from analytics_engine.crypto import cipher_for_user
from analytics_engine.day_dashboard import empty_day
from analytics_engine.jobs import enqueue_job
from analytics_engine.service import inventory_for_user
from analytics_engine.store import current_metadata, read_daily, read_snapshot
from analytics_engine.sync_status import COLLECTION as SYNC_STATUS_COLLECTION, record_upload, status_response

v2 = Blueprint('v2', __name__, url_prefix='/api/v2/')

@v2.before_request
def before_request():
    if request.endpoint == 'v2.login' or request.endpoint == 'v2.refresh':
        return
    
    authorization = request.headers.get('Authorization', '')
    scheme, separator, token = authorization.partition(' ')
    if not separator or scheme.lower() != 'bearer' or not token.strip():
        return jsonify({'error': 'valid bearer token required'}), 401
    token = token.strip()
    db = mongo['hcgateway']
    usrStore = db['users']

    user = usrStore.find_one({'token': token})

    if not user:
        return jsonify({'error': 'invalid token'}), 401
    
    if datetime.datetime.now() > user['expiry']:
        return jsonify({'error': 'token expired. Use /api/v2/login to reauthenticate.'}), 401
    
    g.user = user['_id']

    return

@v2.route("/login", methods=['POST'])
def login(): 
    if not request.json or not 'username' in request.json or not 'password' in request.json:
        return jsonify({'error': 'invalid request'}), 400
    username = request.json['username']
    password = request.json['password']
    fcmToken = request.json['fcmToken'] if 'fcmToken' in request.json else None

    db = mongo['hcgateway']
    usrStore = db['users']

    user = usrStore.find_one({'username': username})

    if not user:
        user = usrStore.insert_one({'username': username, 'password': ph.hash(password)}).inserted_id
        usrStore.insert_one({'_id': str(user), 'username': username, 'password': ph.hash(password)})
        usrStore.delete_one({'_id': ObjectId(user)})

        token = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        expiryDate = datetime.datetime.now() + datetime.timedelta(hours=12)
        usrStore.update_one({'_id': str(user)}, {"$set": {'token': token, 'refresh': refresh, 'expiry': expiryDate}})

        return jsonify({
            "token": token,
            "refresh": refresh,
            "expiry": expiryDate.isoformat()
        }), 201
    
    try:
        ph.verify(user['password'], password)
    except: 
        return jsonify({'error': 'invalid password'}), 403
   
    if fcmToken:
        try:
            usrStore.update_one({'username': username}, {"$set": {'fcmToken': fcmToken}})
        except:
            return jsonify({'error': 'failed to update fcm token'}), 500
        
    sessid = user['_id']

    if not "expiry" in user or datetime.datetime.now() > user['expiry']:
        token = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        expiryDate = datetime.datetime.now() + datetime.timedelta(hours=12)
        usrStore.update_one({'_id': sessid}, {"$set": {'token': token, 'refresh': refresh, 'expiry': expiryDate}})

    else:
        token = user['token']
        refresh = user['refresh']
        expiryDate = user['expiry']

    return jsonify({
            "token": token,
            "refresh": refresh,
            "expiry": expiryDate.isoformat()
    }), 201


@v2.route("/refresh", methods=['POST'])
def refresh():
    if not request.json or not 'refresh' in request.json:
        return jsonify({'error': 'invalid request'}), 400

    refresh = request.json['refresh']

    db = mongo['hcgateway']
    usrStore = db['users']

    user = usrStore.find_one({'refresh': refresh})

    if not user:
        return jsonify({'error': 'invalid refresh token'}), 403
    
    token = secrets.token_urlsafe(32)
    # refresh = secrets.token_urlsafe(32) # disable refresh token rotation- design flaw, see #35
    expiryDate = datetime.datetime.now() + datetime.timedelta(hours=12)
    usrStore.update_one({'_id': user['_id']}, {"$set": {'token': token, 'refresh': refresh, 'expiry': expiryDate}})

    return jsonify({
            "token": token,
            "refresh": refresh,
            "expiry": expiryDate.isoformat()
    }), 200

@v2.route("/revoke", methods=['DELETE'])
def revoke():
    token = request.headers.get('Authorization').split(' ')[1]

    db = mongo['hcgateway']
    usrStore = db['users']

    user = usrStore.find_one({'token': token})

    if not user:
        return jsonify({'error': 'invalid refresh token'}), 403

    usrStore.update_one({'_id': user['_id']}, {"$unset": {'token': 1, 'refresh': 1, 'expiry': 1}})

    return jsonify({
            "success": True
    }), 200

@v2.post("/sync/<method>")
def sync(method):
    if not method:
        return jsonify({'error': 'no method provided'}), 400
    method = method[0].lower() + method[1:]
    if not request.is_json or not request.json or "data" not in request.json:
        return jsonify({'error': 'no data provided'}), 400
    
    userid = g.user
    db = mongo['hcgateway']
    usrStore = db['users']

    try: user = usrStore.find_one({'_id': userid})
    except InvalidId: return jsonify({'error': 'invalid user id'}), 400

    hashed_password = user['password']
    key = base64.urlsafe_b64encode(hashed_password.encode("utf-8").ljust(32)[:32])
    fernet = Fernet(key)

    data = request.json['data']
    if type(data) != list:
        data = [data]
    db = mongo['hcgateway_'+userid]
    collection = db[method]
    collection.create_index([("start", pymongo.ASCENDING), ("app", pymongo.ASCENDING)])
    
    for item in data:
        metadata = item['metadata']
        itemid = metadata['id']
        provenance = {
            key: metadata[key]
            for key in (
                'dataOrigin', 'device', 'recordingMethod', 'lastModifiedTime',
                'clientRecordId', 'clientRecordVersion'
            )
            if key in metadata
        }
        dataObj = {}
        for k, v in item.items():
            if k != "metadata" and k != "time" and k != "startTime" and k != "endTime":
                dataObj[k] = v

        if "time" in item:
            starttime = item['time']
            endtime = None
        else:
            starttime = item['startTime']
            endtime = item['endTime']

        toencrypt = json.dumps(dataObj).encode()
        encrypted = fernet.encrypt(toencrypt).decode()

        # fernet.decrypt(encrypted.encode()).decode()

        try:
            collection.insert_one({"_id": itemid, "id": itemid, 'data': encrypted, "app": metadata['dataOrigin'], "provenance": provenance, "start": starttime, "end": endtime})
        except pymongo.errors.DuplicateKeyError:
            collection.update_one({"_id": itemid}, {"$set": 
                                                 {'data': encrypted, "app": metadata['dataOrigin'], "provenance": provenance, "start": starttime, "end": endtime}
                                                })

    record_upload(mongo['hcgateway'], userid, method, len(data))
    enqueue_job(mongo['hcgateway'], userid, reason='sync')
    return jsonify({'success': True}), 200

@v2.route("/fetch/<method>", methods=['POST'])
def fetch(method):
    if not method:
        return jsonify({'error': 'no method provided'}), 400

    userid = g.user
    db = mongo['hcgateway']
    usrStore = db['users']

    try: user = usrStore.find_one({'_id': userid})
    except InvalidId: return jsonify({'error': 'invalid user id'}), 400

    hashed_password = user['password']
    key = base64.urlsafe_b64encode(hashed_password.encode("utf-8").ljust(32)[:32])
    fernet = Fernet(key)

    if not "queries" in request.json:
        queries = []
    else:
        queries = request.json['queries']
    
    db = mongo['hcgateway_'+userid]
    collection = db[method]
    
    docs = []
    for doc in collection.find(queries):
        doc['data'] = json.loads(fernet.decrypt(doc['data'].encode()).decode())
        docs.append(doc)

    return jsonify(docs), 200


def current_user_and_db():
    user = mongo['hcgateway']['users'].find_one({'_id': g.user})
    if not user:
        return None, None
    return user, mongo['hcgateway_' + g.user]


@v2.get("/analytics/inventory")
def analyticsInventory():
    user, user_db = current_user_and_db()
    if not user:
        return jsonify({'error': 'invalid user id'}), 400
    return jsonify(inventory_for_user(user_db)), 200


@v2.post("/analytics/rebuild")
def analyticsRebuild():
    user, _ = current_user_and_db()
    if not user:
        return jsonify({'error': 'invalid user id'}), 400
    job = enqueue_job(mongo['hcgateway'], g.user, reason='manual', delay_seconds=0)
    return jsonify(job_response(job)), 202


def job_response(job):
    if not job:
        return None
    return {
        'userId': str(job['_id']),
        'status': job.get('status'),
        'reason': job.get('reason'),
        'requestedRevision': job.get('requestedRevision'),
        'requestedAt': job.get('requestedAt').isoformat() if job.get('requestedAt') else None,
        'startedAt': job.get('startedAt').isoformat() if job.get('startedAt') else None,
        'completedAt': job.get('completedAt').isoformat() if job.get('completedAt') else None,
        'error': job.get('error'),
        'result': job.get('result'),
    }


@v2.get("/analytics/status")
def analyticsStatus():
    user, user_db = current_user_and_db()
    if not user:
        return jsonify({'error': 'invalid user id'}), 400
    return jsonify({
        'job': job_response(mongo['hcgateway']['analytics_jobs'].find_one({'_id': g.user})),
        'current': current_metadata(user_db),
        'phoneSync': status_response(mongo['hcgateway'][SYNC_STATUS_COLLECTION].find_one({'_id': str(g.user)})),
    }), 200


@v2.get("/sync/status")
def syncStatus():
    user, _ = current_user_and_db()
    if not user:
        return jsonify({'error': 'invalid user id'}), 400
    document = mongo['hcgateway'][SYNC_STATUS_COLLECTION].find_one({'_id': str(g.user)})
    return jsonify(status_response(document)), 200


@v2.route("/analytics/config", methods=['GET', 'PUT'])
def analyticsConfig():
    user, _ = current_user_and_db()
    if not user:
        return jsonify({'error': 'invalid user id'}), 400
    if request.method == 'GET':
        return jsonify(context_for_user(user).as_dict()), 200
    body = request.get_json(silent=True) or {}
    allowed = {
        'homeTimeZone', 'sleepTargetMinutes', 'birthDate',
        'heartRateZoneThresholds', 'heartRateZoneTestDate',
    }
    if set(body) - allowed:
        return jsonify({'error': 'unsupported analytics configuration field'}), 400
    current = context_for_user(user).as_dict()
    current.update(body)
    try:
        context = validate_context(AnalyticsContext(**current))
    except (TypeError, ValueError) as error:
        return jsonify({'error': str(error)}), 400
    mongo['hcgateway']['users'].update_one({'_id': g.user}, {'$set': {'analyticsConfig': context.as_dict()}})
    job = enqueue_job(mongo['hcgateway'], g.user, reason='configuration', delay_seconds=0)
    return jsonify({'configuration': context.as_dict(), 'job': job_response(job)}), 202


@v2.get("/analytics/snapshot")
def analyticsSnapshot():
    user, user_db = current_user_and_db()
    if not user:
        return jsonify({'error': 'invalid user id'}), 400
    snapshot, current = read_snapshot(user_db, cipher_for_user(user))
    if not snapshot:
        job = mongo['hcgateway']['analytics_jobs'].find_one({'_id': g.user})
        return jsonify({'error': 'analytics not ready', 'job': job_response(job)}), 404
    response = jsonify(snapshot)
    response.set_etag(current['runId'])
    response.headers['Cache-Control'] = 'private, no-cache'
    return response


@v2.get("/analytics/daily")
def analyticsDaily():
    user, user_db = current_user_and_db()
    if not user:
        return jsonify({'error': 'invalid user id'}), 400
    start = request.args.get('start')
    end = request.args.get('end')
    try:
        limit = min(max(int(request.args.get('limit', 400)), 1), 1000)
        if start:
            datetime.date.fromisoformat(start)
        if end:
            datetime.date.fromisoformat(end)
        if start and end and start > end:
            raise ValueError('start must not be after end')
        daily, current = read_daily(user_db, cipher_for_user(user), start, end, limit)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    if not current:
        return jsonify({'error': 'analytics not ready'}), 404
    response = jsonify({'days': daily, 'count': len(daily), 'runId': current['runId']})
    response.set_etag(current['runId'])
    return response


def availability_notes(day):
    notes = []
    groups = (
        ("headlineScores", day.get("headlineScores", {})),
        ("supportingMetrics", day.get("supportingMetrics", {})),
    )
    for group_name, group in groups:
        for field, value in group.items():
            if isinstance(value, dict) and value.get("status") not in ("available", "partial"):
                notes.append({
                    "field": f"{group_name}.{field}",
                    "status": value.get("status"),
                    "note": value.get("note", "Data is unavailable."),
                })
    for field in ("schedule", "targetWakeTime", "targetBedTime"):
        value = day.get("timeline", {}).get(field, {})
        if value.get("status") not in ("available", "partial"):
            notes.append({
                "field": f"timeline.{field}",
                "status": value.get("status"),
                "note": value.get("note", "Data is unavailable."),
            })
    zones = day.get("heartRateZones", {})
    if zones.get("status") not in ("available", "partial"):
        notes.append({"field": "heartRateZones", "status": zones.get("status"), "note": zones.get("note")})
    return notes


@v2.get("/analytics/day")
def analyticsDay():
    user, user_db = current_user_and_db()
    if not user:
        return jsonify({'error': 'invalid user id'}), 400
    context = context_for_user(user)
    try:
        requested_date = request.args.get('date') or datetime.datetime.now(
            datetime.timezone.utc
        ).astimezone(ZoneInfo(context.homeTimeZone)).date().isoformat()
        center = datetime.date.fromisoformat(requested_date)
        radius = int(request.args.get('radius', 7))
        if not 0 <= radius <= 7:
            raise ValueError('radius must be from 0 to 7')
    except (TypeError, ValueError):
        return jsonify({'error': 'date must be YYYY-MM-DD and radius must be from 0 to 7'}), 400
    start_date = center - datetime.timedelta(days=radius)
    end_date = center + datetime.timedelta(days=radius)
    daily, current = read_daily(
        user_db,
        cipher_for_user(user),
        start_date.isoformat(),
        end_date.isoformat(),
        radius * 2 + 1,
    )
    if not current:
        return jsonify({'error': 'analytics not ready'}), 404
    by_date = {item['date']: item.get('dayView') for item in daily if item.get('dayView')}
    days = []
    cursor = start_date
    while cursor <= end_date:
        date = cursor.isoformat()
        item = by_date.get(date) or empty_day(date, context)
        days.append(item)
        cursor += datetime.timedelta(days=1)
    focus = days[radius]
    focus['availabilityNotes'] = availability_notes(focus)
    nearby = [{
        'date': item['date'],
        'dayState': item['dayState'],
        'sleepDuration': item['headlineScores']['sleepDuration'],
        'sleepNeed': item['headlineScores']['sleepNeed'],
        'recovery': item['headlineScores']['recovery'],
        'strain': item['headlineScores']['strain'],
    } for item in days]
    response = jsonify({
        'contractVersion': focus['contractVersion'],
        'day': focus,
        'nearbyDays': nearby,
        'runId': current['runId'],
    })
    response.set_etag(current['runId'])
    response.headers['Cache-Control'] = 'private, no-cache'
    return response

@v2.route("/push/<method>", methods=['PUT'])
def pushData(method):
    if not method:
        return jsonify({'error': 'no method provided'}), 400
    if not "data" in request.json:
        return jsonify({'error': 'no data provided'}), 400

    userid = g.user
    data = request.json['data']
    if type(data) != list:
        data = [data]

    fixedMethodName = method[0].upper() + method[1:]
    for r in data:
        r['recordType'] = fixedMethodName
        if "time" not in r and ("startTime" not in r or "endTime" not in r):
            return jsonify({'error': 'no start time or end time provided. If only one time is to be used, then use the "time" attribute instead.'}), 400
        if ("startTime" in r and "endTime" not in r) or ("startTime" not in r and "endTime" in r):
            return jsonify({'error': 'start time and end time must be provided together.'}), 400

    db = mongo['hcgateway']
    usrStore = db['users']

    try: user = usrStore.find_one({'_id': userid})
    except InvalidId: return jsonify({'error': 'invalid user id'}), 400

    fcmToken = user['fcmToken'] if 'fcmToken' in user else None
    if not fcmToken:
        return jsonify({'error': 'no fcm token found'}), 404

    fcm = FCMNotification(service_account_file='service-account.json', project_id=os.environ['FCM_PROJECT_ID'])

    try:
        fcm.notify(fcm_token=fcmToken, data_payload={
            "op": "PUSH",
            "data": json.dumps(data),
        })
    except Exception as e:
        return jsonify({'error': 'Message delivery failed'}), 500

    return jsonify({'success': True, "message": "request has been sent to device."}), 200

@v2.route("/delete/<method>", methods=['DELETE'])
def delData(method):
    if not method:
        return jsonify({'error': 'no method provided'}), 400
    if not "uuid" in request.json:
        return jsonify({'error': 'no uuid provided'}), 400

    userid = g.user
    uuids = request.json['uuid']
    if type(uuids) != list:
        uuids = [uuids]

    fixedMethodName = method[0].upper() + method[1:]

    db = mongo['hcgateway']
    usrStore = db['users']

    try: user = usrStore.find_one({'_id': userid})
    except InvalidId: return jsonify({'error': 'invalid user id'}), 400

    fcmToken = user['fcmToken'] if 'fcmToken' in user else None
    if not fcmToken:
        return jsonify({'error': 'no fcm token found'}), 404

    fcm = FCMNotification(service_account_file='service-account.json', project_id=os.environ['FCM_PROJECT_ID'])

    try:
        fcm.notify(fcm_token=fcmToken, data_payload={
            "op": "DEL",
            "data": json.dumps({
                "uuids": uuids,
                "recordType": fixedMethodName
            }),
        })
    except Exception as e:
        return jsonify({'error': 'Message delivery failed'}), 500


    return jsonify({'success': True, "message": "request has been sent to device."}), 200


@v2.delete("/sync/<method>")
def delFromDb(method):
    if not method:
        return jsonify({'error': 'no method provided'}), 400
    method = method[0].lower() + method[1:]
    if not request.is_json or not request.json or "uuid" not in request.json:
        return jsonify({'error': 'no uuid provided'}), 400

    userid = g.user
    uuids = request.json['uuid']

    if type(uuids) != list:
        uuids = [uuids]

    db = mongo['hcgateway_'+userid]
    collection = db[method]
    print(collection)
    for uuid in uuids:
        print(uuid)
        try: collection.delete_one({"_id": uuid})
        except Exception as e: print(e)

    enqueue_job(mongo['hcgateway'], userid, reason='delete')
    return jsonify({'success': True}), 200
