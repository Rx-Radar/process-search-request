from firebase_admin import auth, firestore
from google.protobuf import timestamp_pb2
from twilio.rest import Client
from flask import jsonify
import time
import uuid
import yaml
import os


def load_yaml_file(filepath):
    with open(filepath, 'r') as file:
        data = yaml.safe_load(file)
    return data

# Use the function to load the configuration
config = load_yaml_file('config.yaml')

env = os.getenv("deployment_env")

FIREBASE_USERS_DB = config[env]["firebase"]["users_db"]
FIREBASE_CALLS_DB = config[env]["firebase"]["calls_db"]
FIREBASE_SEARCH_REQUESTS_DB = config[env]["firebase"]["search_requests_db"]
FIREBASE_PENDING_SEARCH_REQUESTS_DB = config[env]["firebase"]["pending_search_requests_db"]

CF_GET_PHARMACIES = config[env]["cloud_functions"]["get_pharmacies"]
CF_CREATE_CALL = config[env]["cloud_functions"]["create_call"]


# checks if user is within search request limit today, create new user if non exists
def can_user_search(db, phone_number):
    try: 
        # query for existing user
        matching_users_query_ref = db.collection(FIREBASE_USERS_DB).where('phone', '==', phone_number).limit(1)
        matching_user_docs = matching_users_query_ref.get()

        # if the user doc does not exist, create new user and return true
        if len(matching_user_docs) == 0:
            new_user_uuid = str(uuid.uuid4())

            new_user_doc = {
                "phone": phone_number,
                "user_uuid": new_user_uuid,
                "search_credits": 0,
                "last_search_timestamp": 0
            }
            
            db.collection(FIREBASE_USERS_DB).document(new_user_uuid).set(new_user_doc)
            return True, new_user_uuid
    
        user_doc = matching_user_docs[0].to_dict()

        # get existing user information
        last_search_timestamp = user_doc.get("last_search_timestamp")
        user_uuid = user_doc.get("user_uuid")

        ## ------------- not implemented because payments are in place ---------------------- ##
        # if user should be rate limited
        # SECONDS_IN_DAY = 86400
        # if (time.time() - last_search_timestamp) < SECONDS_IN_DAY:
        #     return False, user_uuid, (jsonify({"error": "User searched too many times"}), 400)
        
        return True, user_uuid, None

    except Exception as e:
        return False, None, (jsonify({
            "status": "error",
            "message": "An error occurred during the search"
        }), 500)

# returns the number of search credits a user has available
def get_user_search_credit(db, user_uuid):
    user_doc_ref = db.collection(FIREBASE_USERS_DB).document(user_uuid)

    # Fetch the user document
    user_doc = user_doc_ref.get()

    # Check if the document exists
    if user_doc.exists:
        search_credits = user_doc.to_dict().get('search_credits')
        return search_credits


# creates a new search request
def db_add_search(db, req_obj, user_uuid, db_location):
    # Generate a unique ID for the document
    search_request_uuid = str(uuid.uuid4())

    # Current epoch time
    epoch_initiated = int(time.time())

    # prescription object
    user_location = req_obj["user_location"]
    med_name = req_obj["prescription"]["name"] 
    med_dosage = req_obj["prescription"]["dosage"]
    med_brand = req_obj["prescription"]["brand_or_generic"]
    med_quantity = req_obj["prescription"]["quantity"]
    med_type = req_obj["prescription"]["type"]

    # Data to be added
    data = {
        "search_request_uuid": search_request_uuid,
        "user_uuid": user_uuid,
        "user_location": user_location,
        "prescription": {
            "name": med_name,
            "dosage": med_dosage,
            "brand": med_brand,
            "quantity": med_quantity,
            "type": med_type
        },
        "epoch_initiated": epoch_initiated,
    }

    # Add the data to a new document in the 'medications' collection
    if db_location == 'PENDING_SEARCH_REQUESTS':
        db.collection(FIREBASE_PENDING_SEARCH_REQUESTS_DB).document(search_request_uuid).set(data)
    else:
        db.collection(FIREBASE_SEARCH_REQUESTS_DB).document(search_request_uuid).set(data)

    # return search_request_uuid
    return search_request_uuid


# verifies user session token
def verify_user_token(token):
    try:
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token['uid']
        # The token is valid
        return uid
    except auth.InvalidIdTokenError:
        # The token is invalid
        return None


# validates user medication request body
def validate_request(request_data):

    # if request is empty return an error
    if not request_data:
        return False, (jsonify({'status': 'error', 'message': 'request is empty'}), 400);

    required_fields = ['user_session_token', 'phone_number', 'user_location', 'prescription'] # required fields
    prescription_fields = ['name', 'dosage', 'brand_or_generic', 'quantity', 'type'] # required fields within medication
    location_fields = ['lat', 'lon'] # required fields within user_location

    # Check if all required fields exist
    for field in required_fields:
        if field not in request_data:
            return False, (jsonify({'status': 'error', 'message': f'Missing required field: {field}'}), 400)

    # Check if the types are correct
    if not isinstance(request_data.get('user_session_token'), str):
        return False, (jsonify({'status': 'error', 'message': 'user_session_token must be a string'}), 400)

    if not isinstance(request_data.get('phone_number'), str):
        return False, (jsonify({'status': 'error', 'message': 'phone_number must be a string'}), 400)

    ## ------------------------------------------- ##
    # Check Location fields and types

    user_location = request_data.get('user_location')

    # check if location is empty
    if not user_location:
        return False, (jsonify({'status': 'error', 'message': 'user_location object cannot be empty'}), 400)
    
    # check that all the prescription fields exist
    for field in location_fields:
        if field not in user_location:
            return False, (jsonify({'status': 'error', 'message': f'Missing required field inside user_location: {field}'}), 400)
    
    if not isinstance(user_location.get('lat'), float):
        return False, (jsonify({'status': 'error', 'message': 'user_location lat must be a float'}), 400)
    if not isinstance(user_location.get('lon'), float):
        return False, (jsonify({'status': 'error', 'message': 'user_location lon must be a float'}), 400)
    
    # make sure the lat and lon values are valid
    if user_location.get('lat') < -90 or user_location.get('lat') > 90 or user_location.get('lon') < -180 or user_location.get('lon') > 180: 
        return False, (jsonify({'status': 'error', 'message': 'user_location lat and lon must be valid'}), 400)

    ## ------------------------------------------- ##
    # Check prescription fields and types

    prescription = request_data.get('prescription')

    # check if prescription is empty 
    if not prescription:
        return False, (jsonify({'status': 'error', 'message': 'prescription object cannot be empty'}), 400)

    # check that all the prescription fields exist
    for field in prescription_fields:
        if field not in prescription:
            return False, (jsonify({'status': 'error', 'message': f'Missing required field inside prescription: {field}'}), 400)

    # check that prescription object field types are valid
    if not isinstance(prescription.get('name'), str):
        return False, (jsonify({'status': 'error', 'message': 'prescription name must be a string'}), 400)
    if not isinstance(prescription.get('dosage'), str):
        return False, (jsonify({'status': 'error', 'message': 'prescription dosage must be a string'}), 400)
    if not isinstance(prescription.get('brand_or_generic'), str):
        return False, (jsonify({'status': 'error', 'message': 'prescription brand_or_generic must be a string'}), 400)
    if not isinstance(prescription.get('quantity'), str):
        return False, (jsonify({'status': 'error', 'message': 'prescription quantity must be a string'}), 400)
    if not isinstance(prescription.get('type'), str):
        return False, (jsonify({'status': 'error', 'message': 'prescription type must be a string'}), 400)

    # on valid
    return True, None
