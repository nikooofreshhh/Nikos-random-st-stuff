from flask import Flask, request, Response, stream_with_context
import requests
from flask_cors import CORS
import os
import time

app = Flask(__name__)
CORS(app) # This enables CORS for all routes

# --- CONFIGURATION ---
FISH_API_KEY = "YOUR FISH KEY HERE"
OPENROUTER_API_KEY = "YOUR OR KEY HERE"
SAVE_DIR = "saved_audio"
os.makedirs(SAVE_DIR, exist_ok=True)

# Use a highly responsive, low-latency model for this intermediate step.
OR_MODEL = "meta-llama/llama-3.1-8b-instruct" 

VOICE_MAP = {
    "narrator": "default",
    "voicename":"unique key" # use the fish voice key here, name the voice what you want in ST
}
# ---------------------

def enhance_text_with_tags(original_text):
    """Sends the text to OpenRouter to intelligently inject Fish Audio tone tags."""
    if not original_text or not original_text.strip():
        return original_text

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # System prompt specifically designed for TTS preparation
    system_prompt = (
        "You are an emotionally attuned TTS pre-processor. Read the provided scene and insert any of the following emotional tone tags:"
        " [angry], [sad], [embarrassed], [emphasis], [whispering], [soft], [breathy], [excited] or any of the following voice effect tags:"
        " [laughing], [chuckling], [moaning], [clear throat], [sobbing], [crying loudly], [sighing], [panting], [groaning], [growling], [pause], [long pause]"
        " before important sentences or clauses in the text. Insert at most 1 tags per sentence; leave out tags when emotional tone unneeded based on context. Use only provided tags." 
        " Tags are inserted BEFORE sentences or clauses where they should be applied. Original text remains UNALTERED aside from tag insertion."
        " Only output the final tagged text. Do not include any explanations, quotes, or conversational filler."
    )

    payload = {
        "model": OR_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Text to enhance:\n{original_text}"}
        ],
        "temperature": 0.3 # Keep it low for deterministic, instruction-following behavior
    }

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions", #you can change this to Nano or whatever
            headers=headers,
            json=payload,
            timeout=5 # Prevent it from hanging indefinitely and breaking TTS
        )
        response.raise_for_status()
        
        # Extract the rewritten text from the OpenRouter response
        enhanced_text = response.json()['choices'][0]['message']['content'].strip()
        print(f"Original: {original_text}\nEnhanced: {enhanced_text}") #this prints to terminal so you can adjust the prompt
        return enhanced_text
        
    except Exception as e:
        print(f"OpenRouter Enhancement Error: {e}")
        # If the LLM fails, fallback to the original text so audio still plays
        return original_text

@app.route('/v1/audio/speech', methods=['POST'])
def tts():
    data = request.json
    raw_text = data.get('input', '')
    requested_voice = data.get('voice', 'narrator') 
    
    # 1. Enhance the text via OpenRouter BEFORE building the Fish Payload
    processed_text = enhance_text_with_tags(raw_text)

    # 2. Build the Fish Audio payload, change up settings if you want
    fish_payload = {
        "text": processed_text,
        "format": "mp3",
        "model": "drama-3", 
        "temperature": 0.8,
        "prosody": {
            "speed": 1,
            "volume": 0,
            "normalize_loudness": True # Corrected to Python boolean
        }
    }
    
    fish_reference_id = VOICE_MAP.get(requested_voice, requested_voice)
    if fish_reference_id and fish_reference_id != "default":
        fish_payload["reference_id"] = fish_reference_id

    headers = {
        "Authorization": f"Bearer {FISH_API_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(
            "https://api.fish.audio/v1/tts",
            headers=headers,
            json=fish_payload,
            stream=True
        )
        response.raise_for_status()
        
        # Generator to write the chunk to a file, then pass it along to ST- you can replay your tts, this part is optional
        def generate_and_save():
            timestamp = int(time.time())
            # Optional: sanitize processed_text here if you want it in the filename, 
            # but a timestamp + voice name is usually safer for filepaths.
            filepath = os.path.join(SAVE_DIR, f"tts_{requested_voice}_{timestamp}.mp3")
            
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)
                        yield chunk
        
        return Response(
            stream_with_context(generate_and_save()), 
            content_type='audio/mpeg'
        )
        
    except Exception as e:
        print(f"Fish TTS Error: {e}")
        return {"error": str(e)}, 500


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000)
    
