from flask import Flask, render_template, request, jsonify
import os
from datetime import datetime
import requests
import firebase_admin
from firebase_admin import credentials, firestore

# Setup Flask app
app = Flask(__name__)

# Firebase setup
import json
import os
from firebase_admin import credentials, initialize_app

firebase_creds_json = os.environ.get('FIREBASE_CREDS')
firebase_creds = json.loads(firebase_creds_json)
cred = credentials.Certificate(firebase_creds)
initialize_app(cred)

db = firestore.client()

GEMINI_API_KEY = 'AIzaSyAKujRs-zZ6HeGLK9sqCqz-tvFBd6ZgEFw'

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/log', methods=['POST'])
def log_day():
    user_id = request.json.get('user_id')
    status = request.json.get('status')
    custom_date = request.json.get('date')
    today = custom_date if custom_date else datetime.now().strftime('%Y-%m-%d')

    if not user_id or not status:
        return jsonify({'message': 'Missing user_id or status'}), 400

    user_ref = db.collection('users').document(user_id)
    entries_ref = user_ref.collection('entries')
    cycles_ref = user_ref.collection('cycles')
    meta_ref = user_ref.collection('meta').document('state')

    # Prevent duplicate entries
    if entries_ref.document(today).get().exists:
        return jsonify({'message': 'Already logged for this date'}), 400

    # Save the new entry
    entry = {'date': today, 'status': status}
    entries_ref.document(today).set(entry)

    # Load current cycle from meta
    meta_doc = meta_ref.get()
    current_cycle = meta_doc.to_dict().get('current_cycle', []) if meta_doc.exists else []

    gemini_response = ""

    if status == 'period':
        if any(e['status'] == 'no_period' for e in current_cycle):
            # Previous cycle has ended
            last_cycle = current_cycle
            period_days = sum(1 for e in last_cycle if e['status'] == 'period')
            total_days = len(last_cycle)

            if total_days > 0:
                cycle_info = {
                    'start': last_cycle[0]['date'],
                    'end': last_cycle[-1]['date'],
                    'period_days': period_days,
                    'total_days': total_days
                }
                cycles_ref.add(cycle_info)

                # Get all past cycles to check if at least 2 exist
                past_cycles = list(cycles_ref.order_by('start').stream())
                if len(past_cycles) >= 2:
                    prev_cycle = past_cycles[-2].to_dict()
                    gemini_response = send_to_gemini(prev_cycle)

            # Start a new cycle with current entry
            current_cycle = [entry]
        else:
            # Still part of ongoing period
            current_cycle.append(entry)

    elif status == 'no_period':
        current_cycle.append(entry)

    # Save updated current_cycle back to Firestore
    meta_ref.set({'current_cycle': current_cycle})

    return jsonify({'message': 'Logged successfully', 'gemini_response': gemini_response}), 200




def send_to_gemini(cycles):
    summaries = []
    for i, cycle in enumerate(cycles, start=1):
        summaries.append(
            f"Cycle {i}: Start: {cycle['start']}, End: {cycle['end']}, "
            f"Total Days: {cycle['total_days']}, Bleeding Days: {cycle['period_days']}"
        )
    cycles_summary = "\n".join(summaries)

    prompt = (
        f"Here are the user's past menstrual cycles:\n\n{cycles_summary}\n\n"
        "Do these cycles indicate any irregularity or concern? Are they within the normal range of 21 to 34 days? Limit your answer to 25 words."
    )

    return call_gemini(prompt)

@app.route('/chat', methods=['POST'])
def chat():
    user_id = request.json.get('user_id')
    user_message = request.json.get('message', '')

    if not user_id or not user_message:
        return jsonify({'response': 'Missing user_id or message'}), 400

    cycles_ref = db.collection('users').document(user_id).collection('cycles')
    cycles_docs = list(cycles_ref.order_by('start').stream())

    if not cycles_docs:
        context = "There is no recorded cycle data yet."
    else:
        summaries = []
        for i, doc in enumerate(cycles_docs, start=1):
            cycle = doc.to_dict()
            summaries.append(
                f"Cycle {i}: Start: {cycle['start']}, End: {cycle['end']}, "
                f"Total Days: {cycle['total_days']}, Bleeding Days: {cycle['period_days']}"
            )
        context = "Here are the user's past menstrual cycles:\n\n" + "\n".join(summaries)

    prompt = (
        "You are a helpful assistant. Use the menstrual cycle data to answer the user's question. "
        "No formatting. Limit to 25 words.\n\n"
        + context + "\n\nUser question: " + user_message
    )

    ai_response = call_gemini(prompt)
    return jsonify({'response': ai_response})

def call_gemini(prompt):
    url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}'
    headers = {'Content-Type': 'application/json'}
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        result = response.json()
        return result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
    except Exception as e:
        print("Gemini API error:", e)
        return "Failed to get response from Gemini."

if __name__ == '__main__':
    app.run(debug=True)
