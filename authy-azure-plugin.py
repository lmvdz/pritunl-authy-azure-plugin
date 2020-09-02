import os
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())


from pritunl import logger
# Authy Client API
from authy.api import AuthyApiClient
authy_api = AuthyApiClient(os.getenv("AUTHY_API"))

import time
import pymongo
import requests
import re
import phonenumbers

# MongoDB Client API
from pymongo import MongoClient
mongoClient = MongoClient(os.getenv("MONGODB_HOST"), os.getenv("MONGODB_PORT"))
userDB = mongoClient.pritunl.users


mgraphSecret = os.getenv("GRAPH_SECRET")
mgraphAppID = os.getenv("GRAPH_APP_ID")
mgraphTenant = os.getenv("TENANT_ID")
pritunlGroupBypass = os.getenv("GROUP_BYPASS").split(' ')

URL = 'https://login.microsoftonline.com/'+mgraphTenant+'/oauth2/v2.0/token'
data = {'client_id': mgraphAppID, 'scope': 'https://graph.microsoft.com/.default', 'client_secret': mgraphSecret, 'grant_type': 'client_credentials'}
response = requests.post(url = URL, data = data)
response_data = response.json()
access_token_expire = response_data['expires_in']
access_token_retrieved_seconds = int(round(time.time() * 1000)) / 1000
access_token = response_data['access_token']




def user_connect(host_id, server_id, org_id, user_id, host_name,
        server_name, org_name, user_name, remote_ip, platform, device_name,
        password, **kwargs):
        # PRITUNL USER GROUP BYPASS AUTHY
        if user_name in pritunlGroupBypass:
            return True, "Pritunl Group Bypass"
        
        # SETUP GLOBAL VAR
        global access_token_retrieved_seconds
        global access_token_expire
        global access_token

        # SETUP AUTHY FIELD FOR USER
        userDB.update({'name': user_name, 'authy_id': {'$exists': False}}, {'$set': {'authy_id': ''}})

        # FIND USER IN DB
        user = userDB.find_one({'name': user_name})
        logger.info('user trying to connect: ' + user_name);

        # TEMP AUTHY_ID
        authy_id = 0

        # CHECK FOR ACCESS_TOKEN EXPIRATION
        current_time_in_seconds = int(round(time.time() * 1000)) / 1000
        if (current_time_in_seconds - access_token_retrieved_seconds) > access_token_expire:
            URL = 'https://login.microsoftonline.com/'+mgraphTenant+'/oauth2/v2.0/token'
            data = {'client_id': mgraphAppID, 'scope': 'https://graph.microsoft.com/.default', 'client_secret': mgraphSecret, 'grant_type': 'client_credentials'}
            response = requests.post(url = URL, data = data)
            response_data = response.json()
            access_token_expire = response_data['expires_in']
            access_token_retrieved_seconds = int(round(time.time() * 1000)) / 1000
            access_token = response_data['access_token']


        # AUTHY_ID SETUP
        if user['authy_id'] == '':
            
            # SETUP AUTHY ID
            logger.info('setting up authy_id for ' + user_name);

            # Retrieve Mobile Phone from Microsoft Graph
            URL = 'https://graph.microsoft.com/v1.0/users/'+user_name
            response = requests.get(url = URL, headers={'Authorization': 'Bearer '+access_token})
            response_data = response.json()

            # Mobile Phone and Country Code Valid?
            only_number = ''.join(re.findall('[0-9]+', response_data['mobilePhone']));
            logger.info('parsed phone number from o365 '+ str(only_number) + ' for user ' + user_name, 'authy');
            e_one_six_four = phonenumbers.parse('+' + only_number, None)
            is_valid_number = phonenumbers.is_valid_number(e_one_six_four)
            if is_valid_number:
                # Parse Mobile Phone and Country Code from E.164 Standardized PhoneNumber object
                country_code = e_one_six_four.country_code
                mobile_phone = e_one_six_four.national_number

                # SETUP NEW USER IN AUTHY
                newUser = authy_api.users.create(
                    email=user_name,
                    phone=str(mobile_phone),
                    country_code=country_code)

                # SETUP OF NEW USER?
                if newUser.ok():
                    # UPDATE MONGODB WITH AUTHY_ID
                    logger.info('Registered new authy user associated with: '+user_name, 'authy')
                    userDB.update({'name': user_name}, {'$set': {'authy_id': newUser.id}})
                else:
                    # COULD NOT SETUP NEW USER IN AUTHY
                    logger.warning('new Authy user not ok', 'authy')
                    logger.warning(newUser.errors(), 'authy')
            else:
                # PHONE NUMBER NOT VALID IN OFFICE 365
                logger.error(user_name + ' does not have a valid phone number in o365: ' + str(e_one_six_four), 'authy')
                return False, user_name + ' does not have a valid phone number in o365'
        else:
            logger.info('User already has authy_id, '+str(user['authy_id']), 'authy');
        # START PUSH NOTIFICATION 2FA
        logger.info('Sent Authy Push Notification to: ' + user_name, 'authy')
        # SETUP AUTHY PAYLOAD
        details = {
            'Username': user_name
        }

        hidden_details = {}

        logos = [
            dict(res='default', url=os.getenv("AUTHY_ONETOUCH_LOGO_HIGH_RES")),
            dict(res='low', url=os.getenv("AUTHY_ONETOUCH_LOGO_LOW_RES"))
        ]
        # GET THE USER FROM MONGODB
        user = userDB.find_one({'name': user_name})
        # DOES THE USER EXIST STILL?
        if not user:
            return False, "No user found"
        # GET AUTHY ID FROM USER
        authy_id = user['authy_id']
        if authy_id != '':
            # SEND ONETOUCH REQUEST TO USER'S AUTHY
            push_notification = authy_api.one_touch.send_request(
                authy_id,
                os.getenv("AUTHY_ONETOUCH_MESSAGE"),
                seconds_to_expire=60,
                details=details,
                hidden_details=hidden_details,
                logos=logos)
            # PUSH NOTIFICATION SENT TO USER?
            if push_notification.ok():
                # CHECK EVERY sleep_time SECONDS FOR push_notification STATUS
                sleep_timeout = 60
                sleep_interval_index = 1
                sleep_time = 5
                while sleep_interval_index * sleep_time < sleep_timeout:
                    time.sleep(sleep_time)
                    status = authy_api.one_touch.get_approval_status(push_notification.get_uuid())
                    # STATUS APPROVED?
                    if status.content['approval_request']['status'] == "approved":
                        logger.info(user_name+' Authy 2FA Approved', 'authy')
                        return True, None
                    else:
                        sleep_interval_index += 1
                logger.info(user_name +' failed to approve the Authy 2FA Push Notification', 'authy');
                return False, "Authy 2FA Failed"
            else:
                logger.info('Authy 2FA Push Notification not okay for user: ' + user_name, 'authy');
        else:
            logger.info('No Authy ID found for user: ' + user_name, 'authy');
            return False, "No Authy ID found for user: "+user_name