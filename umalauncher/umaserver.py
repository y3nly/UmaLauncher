from flask import Flask, request, send_file, send_from_directory
from werkzeug.serving import make_server
from loguru import logger
import json
import threading
import util

domain = '127.0.0.1'
port = 3150

app = Flask(__name__)
threader = None

@app.route('/')
def index():
    return 'Hello World!'


@app.route('/training-helper')
def training_helper():
    asset_path = util.get_asset("_assets/training_helper/index.html")
    return send_file(
        asset_path,
        mimetype="text/html",
        max_age=0,
    )


@app.route('/training-helper/assets/<path:filename>')
def training_helper_asset(filename):
    return send_from_directory(
        util.get_asset("_assets/training_helper"),
        filename,
        max_age=86400,
    )

# @app.route('/open-skill-window', methods=['OPTIONS'])
# def open_skills_window_options():
#     return '', 200

@app.route('/open-skill-window', methods=['POST'])
def open_skills_window():
    global threader
    if threader.carrotjuicer:
        threader.carrotjuicer.open_skill_window = True

    return '', 200

@app.route('/open-event-window', methods=['POST'])
def open_event_window():
    global threader
    if threader.carrotjuicer:
        threader.carrotjuicer.open_event_window = True

    return '', 200

@app.route('/skill-window-cm-definition', methods=['POST'])
def skill_window_cm_definition():
    global threader
    cm_definition = request.data.decode('utf-8').strip()
    if threader.carrotjuicer:
        try:
            selected_cm_definition = int(cm_definition)
        except ValueError:
            selected_cm_definition = 16

        if selected_cm_definition not in (16, 17):
            selected_cm_definition = 16

        threader.carrotjuicer.selected_cm_definition = selected_cm_definition
        threader.carrotjuicer.open_skill_window = True

    return '', 200

@app.route('/rerun-skill-simulation', methods=['POST'])
def rerun_skill_simulation():
    global threader
    if threader.carrotjuicer:
        threader.carrotjuicer.request_skill_simulation_rerun()

    return '', 200

@app.route('/open-schedule-window', methods=['POST'])
def open_schedule_window():
    global threader
    if threader.carrotjuicer:
        threader.carrotjuicer.open_schedule_window = True

    return '', 200

@app.route('/helper-window-rect', methods=['POST'])
def helper_window_rect():
    global threader
    # Json is sent as text/plain in body.
    json_data = json.loads(request.data.decode('utf-8'))
    
    if threader.carrotjuicer:
        threader.carrotjuicer.record_window_rect("helper", json_data)

    return '', 200

@app.route('/event-chain-nav', methods=['POST'])
def event_chain_nav():
    global threader
    try:
        payload = json.loads(request.data.decode('utf-8'))
    except (TypeError, ValueError, json.JSONDecodeError):
        return 'Invalid event chain navigation', 400

    if not isinstance(payload, dict):
        return 'Invalid event chain navigation', 400
    direction = payload.get("direction")
    generation = payload.get("generation")
    if (
        isinstance(direction, bool)
        or direction not in (-1, 1)
        or isinstance(generation, bool)
        or not isinstance(generation, int)
    ):
        return 'Invalid event chain navigation', 400

    if threader.carrotjuicer:
        threader.carrotjuicer.navigate_event_chain(direction, generation)

    return '', 200

@app.route('/skills-window-rect', methods=['POST'])
def skills_window_rect():
    global threader
    # Json is sent as text/plain in body.
    json_data = json.loads(request.data.decode('utf-8'))
    
    if threader.carrotjuicer:
        threader.carrotjuicer.record_window_rect("skill", json_data)

    return '', 200

@app.route('/events-window-rect', methods=['POST'])
def events_window_rect():
    global threader
    # Json is sent as text/plain in body.
    json_data = json.loads(request.data.decode('utf-8'))

    if threader.carrotjuicer:
        threader.carrotjuicer.record_window_rect("events", json_data)

    return '', 200

@app.route('/schedule-window-rect', methods=['POST'])
def schedule_window_rect():
    global threader
    # Json is sent as text/plain in body.
    json_data = json.loads(request.data.decode('utf-8'))
    
    if threader.carrotjuicer:
        threader.carrotjuicer.record_window_rect("schedule", json_data)

    return '', 200

@app.route('/topmost', methods=['POST'])
def topmost():
    global threader
    # Json is sent as text/plain in body.
    json_data = json.loads(request.data.decode('utf-8'))

    if threader.carrotjuicer:
        threader.carrotjuicer.set_browser_topmost(json_data)
    return '', 200

@app.route('/pair', methods=['POST'])
def pair():
    global threader
    is_paired = json.loads(request.data.decode('utf-8'))

    if threader.carrotjuicer:
        threader.carrotjuicer.set_browser_pair(is_paired)
    return '', 200






class UmaServer():

    def __init__(self, incoming_threader):
        global threader
        self.server = None
        self.ready = threading.Event()
        self.startup_error = None
        threader = incoming_threader

    def run_with_catch(self):
        try:
            self.run()
        except Exception as exc:
            failed_during_startup = not self.ready.is_set()
            if failed_during_startup:
                self.startup_error = exc
            self.ready.set()
            if not failed_during_startup:
                util.show_error_box("Critical Error", "Uma Launcher has encountered a critical error and will now close.")

    def run(self):
        logger.info("Starting server")
        self.server = make_server(domain, port, app, threaded=True)
        self.ready.set()
        self.server.serve_forever()

    def stop(self):
        logger.info("Stopping server")
        if self.server:
            self.server.shutdown()
        self.ready.set()
        logger.info("Server stopped")
