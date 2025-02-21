import os
import time
import logging
from flask import Flask, render_template, request, jsonify, g, send_file, Response, session
from flask_session import Session
from Brain_modules.brain import Brain
from listen_lobe import AuroraRecorder
from speaker import text_to_speech
from queue import Queue
import json
from Brain_modules.llm_api_calls import llm_api_calls, tools
from Brain_modules.image_vision import ImageVision
from Brain_modules.memory_utils import setup_embedding_collection, setup_logging



app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = './flask_session/'
Session(app)

# Initialize components
progress_queue = Queue()
collection, collection_size = setup_embedding_collection()
brain = Brain(progress_queue.put, collection, collection_size)
aurora_recorder = AuroraRecorder()
image_vision = ImageVision()

def update_progress(message):
    """Update the progress queue with a new message."""
    logging.debug(f"Progress update: {message}")
    progress_queue.put(message)

def process_input(input_text, session_id):
    """Process the input text through the Brain module."""
    if not input_text:
        update_progress("Error: No input provided.")
        return {'response': 'No input provided.', 'status': 'Error'}, None
    
    update_progress(f"Received input: {input_text}")

    try:
        response = brain.process_input(input_text, session_id)
        update_progress("Response generated")
        audio_file = None
        if brain.tts_enabled:
            update_progress("Generating audio response...")
            audio_file = text_to_speech(response)
            update_progress("Audio response generated")
        return {'response': response, 'status': 'Completed'}, audio_file
    except Exception as e:
        error_message = f"Error processing input: {str(e)}"
        update_progress(error_message)
        return {'response': error_message, 'status': 'Error'}, None

@app.before_request
def before_request():
    """Log the start time of the request."""
    g.start_time = time.time()

@app.after_request
def after_request(response):
    """Log the time taken to process the request."""
    diff = time.time() - g.start_time
    logging.debug(f"Request processed in {diff:.2f} seconds")
    return response

@app.route('/')
def index():
    """Render the main index page."""
    return render_template('index.html')

@app.route('/update_api_provider', methods=['POST'])
def update_api_provider():
    data = request.json
    provider = data.get('provider')
    if provider:
        llm_api_calls.update_api_provider(provider)
        return jsonify({"status": "success", "message": f"API provider updated to {provider}"})
    else:
        return jsonify({"status": "error", "message": "No provider specified"}), 400

@app.route('/send_message', methods=['POST'])
def send_message():
    """Handle sending a message."""
    data = request.json
    message = data.get('message')
    session_id = session.get('session_id', str(time.time()))
    session['session_id'] = session_id
    
    if not message:
        return jsonify({'response': 'No message provided.', 'status': 'Error'})
    
    # Clear the progress queue before processing
    while not progress_queue.empty():
        progress_queue.get()
    
    response, audio_file = process_input(message, session_id)
    return jsonify({**response, 'audio_file': audio_file})

@app.route('/toggle_tts', methods=['POST'])
def toggle_tts():
    """Toggle the Text-to-Speech (TTS) functionality."""
    try:
        status = brain.toggle_tts()
        logging.debug(f"TTS toggled to {status}")
        return jsonify({'status': f'Text-to-Speech {status}'})
    except Exception as e:
        error_message = f"Error toggling TTS: {str(e)}"
        logging.error(error_message)
        return jsonify({'status': 'Error', 'error': error_message})

@app.route('/start_recording', methods=['POST'])
def start_recording():
    """Start recording audio."""
    try:
        aurora_recorder.start_recording()
        return jsonify({'status': 'Recording started'})
    except Exception as e:
        return jsonify({'status': f"Error starting recording: {str(e)}"})

@app.route('/stop_recording', methods=['POST'])
def stop_recording():
    """Stop recording audio and process the transcription."""
    try:
        update_progress("Stopping recording...")
        aurora_recorder.stop_recording()
        update_progress("Recording stopped, starting transcription...")
        transcription = aurora_recorder.transcription
        update_progress(f"Transcription completed: {transcription}")
        
        session_id = session.get('session_id', str(time.time()))
        session['session_id'] = session_id
        response, audio_file = process_input(transcription, session_id)
        
        return jsonify({**response, 'transcription': transcription, 'audio_file': audio_file})
    except Exception as e:
        error_message = f"Error stopping recording: {str(e)}"
        update_progress(error_message)
        return jsonify({'status': error_message, 'response': error_message})

@app.route('/get_audio/<filename>', methods=['GET'])
def get_audio(filename):
    """Serve the generated audio file."""
    return send_file(filename, mimetype="audio/mp3")

@app.route('/get_detailed_info', methods=['GET'])
def get_detailed_info():
    """Return detailed information from the brain module."""
    return brain.get_detailed_info()

@app.route('/progress_updates')
def progress_updates():
    """Provide progress updates as a server-sent event stream."""
    def generate():
        while True:
            message = progress_queue.get()
            logging.debug(f"Sending SSE: {message}")
            yield f"data: {json.dumps({'message': message})}\n\n"
    return Response(generate(), mimetype='text/event-stream')

@app.route('/chat_history')
def chat_history():
    """Return the chat history as a JSON response."""
    session_id = session.get('session_id')
    if session_id:
        history = brain.get_chat_history(session_id)
        return jsonify(history)
    return jsonify([])

@app.route('/set_env', methods=['POST'])
def set_env():
    data = request.json
    variable = data.get('variable')
    value = data.get('value')
    if variable and value:
        os.environ[variable] = value
        return jsonify({'status': 'success', 'message': f'{variable} has been set'})
    return jsonify({'status': 'error', 'message': 'Invalid request. Both variable and value are required.'}), 400

@app.route('/analyze_image', methods=['POST'])
def analyze_image():
    """Analyze an image using the image_vision module."""
    data = request.json
    image_url = data.get('image_url')
    if not image_url:
        return jsonify({'status': 'error', 'message': 'No image URL provided'}), 400
    
    try:
        analysis = image_vision.analyze_image(image_url)
        return jsonify({'status': 'success', 'analysis': analysis})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, threaded=True, debug=False, use_reloader=False)