import urllib.request
import json

def get_temperature(city):
    """Fetch temperature from external API."""
    url = f"https://api.example.com/weather?city={city}"
    with urllib.request.urlopen(url) as response:
        data = json.loads(response.read())
    return data["temperature"]
