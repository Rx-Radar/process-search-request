import functions_framework
from flask import jsonify
from firebase_admin import credentials, firestore, initialize_app
from packages import util
from twilio.rest import Client
import yaml
import os

def load_yaml_file(filepath):
    with open(filepath, 'r') as file:
        data = yaml.safe_load(file)
    return data

# Use the function to load the configuration
config = load_yaml_file('config.yaml')

env = os.getenv("deployment_env")

TWILIO_ACCOUNT_SID = config[env]["twilio"]["account_sid"] 
TWILIO_AUTH_TOKEN = config[env]["twilio"]["auth_token"] 

# Initialize Firebase Admin SDK with the service account key
cred = credentials.Certificate("firebase_creds.json")  # Update with your service account key file 
initialize_app(cred)
db = firestore.client() # set firestore client

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

"""
{
    "user_session_token": "12345abcde",
    "phone_number": "+12032248444",
    "user_location": {
        lat: 0.00,
        lon: 0.00,
    },
    "prescription": {
        "name": "Focalin",
        "dosage": "10",
        "brand_or_generic": "Generic",
        "quantity": "30",
        "type": "Extended%20Release"
    }
}
"""
@functions_framework.http
def main(request):
    
    # Set CORS headers for the preflight request
    if request.method == "OPTIONS":
        headers = {
            "Access-Control-Allow-Origin": "*", # change from "*" to "https://rx-radar.com" for production
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "3600",
        }
        return ("", 204, headers)

    # Set CORS headers for the main request
    headers = {"Access-Control-Allow-Origin": "*"} # change from "*" to "https://rx-radar.com" for production 

    # Get the JSON data from the request
    request_data = request.get_json(silent=True)

    # If the token is valid, proceed with the request processing
    success, out, code = util.validate_request(request_data)
    if not success:
        return out, code, headers

    # verify the user session token
    user_session_token = request_data["user_session_token"]
    verification_token = util.verify_user_token(token=user_session_token)
    if not verification_token:
        # If the user session token is incorrect, return a 401 Unauthorized response
        return jsonify({'error': 'Unauthorized'}), 401, headers

    phone_number = request_data["phone_number"]

    # checks that the user is valid to place calls 
    user_can_search, user_uuid, err = util.can_user_search(db, phone_number)
    if not user_can_search: 
        return err, headers
    
    if util.get_user_search_credit(db=db, user_uuid=user_uuid) > 0:
        # Push new search to search_requests
        util.db_add_search(db, request_data, user_uuid, 'SEARCH_REQUESTS')
        return jsonify({'message': 'Request is valid dont show payment'}), 200, headers

    # Push new search to pending_search_requests
    util.db_add_search(db, request_data, user_uuid, 'PENDING_SEARCH_REQUESTS')

    return jsonify({'message': 'Request is valid show payment'}), 200, headers




        