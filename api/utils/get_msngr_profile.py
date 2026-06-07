import requests

def get_messenger_profile(psid, access_token):
    url = f"https://graph.facebook.com/v19.0/{psid}"

    params = { 
        "fields": "first_name,last_name,profile_pic",
        "access_token": access_token,
    }

    response = requests.get(url, params=params)

    if response.status_code != 200:
        raise Exception(f"Failed to get Messenger profile: {response.status_code} {response.text}")
    
    data = response.json()
    
    return {
        "first_name": data.get("first_name", ""),
        "last_name": data.get("last_name", ""),
        "profile_pic": data.get("profile_pic", ""),
    }