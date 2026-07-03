#!/bin/bash
# Start the LeadPilot API. Activate your virtualenv first (see README), then:
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
