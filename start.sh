#!/bin/bash
cd ~/email-unsubscriber
source venv/bin/activate
uvicorn main:app
