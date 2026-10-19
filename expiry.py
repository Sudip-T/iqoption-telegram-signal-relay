import time
from datetime import datetime, timedelta

def calculate_minutes(expiration_time, open_time=None):
    """Calculate minutes remaining or total minutes"""
    
    if open_time:
        # Convert milliseconds to seconds if needed
        if open_time > 1000000000000:  # If it's in milliseconds (timestamp > year 2000 in ms)
            open_time_seconds = open_time / 1000
        else:
            open_time_seconds = open_time
            
        # Calculate total duration
        duration_seconds = expiration_time - open_time_seconds
        minutes = duration_seconds / 60
        print(f"Total duration: {minutes:.2f} minutes ({duration_seconds:.0f} seconds)")
        return duration_seconds, minutes
    else:
        # Calculate time remaining
        now = time.time()
        remaining_seconds = expiration_time - now
        minutes = remaining_seconds / 60
        print(f"Time remaining: {minutes:.2f} minutes ({remaining_seconds:.0f} seconds)")
        return remaining_seconds, minutes