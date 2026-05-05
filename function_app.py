"""Azure Functions entrypoints for RawatAI.

Keep this file as a thin shell only: triggers/routes call helper modules.
"""

import json
import logging

import azure.functions as func

from scheduler_service import build_aligned_response, run_scheduler_batch

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("ok", status_code=200)


@app.route(route="respond", methods=["POST"])
def respond(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body", status_code=400)

    status, result = build_aligned_response(payload)
    if status != 200:
        return func.HttpResponse(str(result), status_code=status)

    return func.HttpResponse(json.dumps(result), status_code=200, mimetype="application/json")


@app.timer_trigger(
    schedule="0 */5 * * * *",
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True,
)
def scheduler(mytimer: func.TimerRequest) -> None:
    if mytimer.past_due:
        logging.warning("scheduler trigger is running late")

    logging.info("scheduler trigger fired")
    sent = run_scheduler_batch()
    logging.info("scheduler proactive nudges queued: %s", sent)
