from geopy.geocoders import Nominatim

def get_coordinates(place):
    """
    Some feeds are missing coordinates, especially for stops outside of "their" country.
    This function geocodes the station names to allow stop matching while merging feeds.
    """

    geolocator = Nominatim(user_agent="city_mapper", timeout=10)
    try:
        location = geolocator.geocode(place)
        if location:
            return location.latitude, location.longitude
    except Exception as e:
        print(f"Error with {place}: {e}")
    return None, None