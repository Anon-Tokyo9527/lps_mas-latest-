import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from .web_console import render_console_html


class ApiRequestError(Exception):
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


class LPBThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class HtmlResponse:
    def __init__(self, html):
        self.html = html


class LPBApiHandler(BaseHTTPRequestHandler):
    server_version = "LPBConfigApi/0.1"
    scenario = None

    def do_OPTIONS(self):
        self._send_json(204, {})

    def do_GET(self):
        self._handle(self._handle_get)

    def do_POST(self):
        self._handle(self._handle_post)

    def log_message(self, fmt, *args):
        print("[LPB API] " + fmt % args)

    def _handle(self, handler):
        started_at = time.time()
        status_code = 200
        response_payload = None
        self._request_json_payload = None
        try:
            response_payload = handler()
            if isinstance(response_payload, HtmlResponse):
                self._send_html(200, response_payload.html)
            else:
                self._send_json(200, response_payload)
        except ApiRequestError as exc:
            status_code = exc.status_code
            response_payload = {"detail": exc.detail}
            self._send_json(exc.status_code, {"detail": exc.detail})
        except Exception as exc:
            status_code = 500
            response_payload = {"detail": str(exc)}
            self._send_json(500, {"detail": str(exc)})
        finally:
            self._log_http_request(started_at, status_code, response_payload)

    def _handle_get(self):
        parts = self._path_parts()

        if not parts or parts == ["docs"]:
            return self._api_index()

        if parts == ["console"]:
            return HtmlResponse(render_console_html(self.scenario.get_default_command_script_path()))

        if parts == ["health"]:
            return {
                "ok": True,
                "loaded_config_path": self.scenario.loaded_config_path,
                "candidate_count": len(self.scenario.get_candidate_pool()),
            }

        if parts == ["state"]:
            return self.scenario.get_api_state()

        if parts == ["debug", "snapshot"]:
            query = self._query_params()
            return self.scenario.get_debug_snapshot(
                include_world=query.get("world", ["1"])[0] not in ("0", "false", "False"),
                include_tasks=query.get("tasks", ["1"])[0] not in ("0", "false", "False"),
            )

        if parts == ["runtime"]:
            return self.scenario.get_runtime_status()

        if parts == ["logs"]:
            query = self._query_params()
            return self.scenario.get_logs(
                since=query.get("since", [None])[0],
                limit=query.get("limit", [200])[0],
                level=query.get("level", [None])[0],
            )

        if len(parts) == 2 and parts[0] == "tasks":
            task = self.scenario.get_api_task(parts[1])
            if not task:
                raise ApiRequestError(404, f"Task not found: {parts[1]}")
            return task

        if parts == ["candidate_pool"]:
            return self.scenario.get_candidate_pool()

        if parts == ["scripts", "status"]:
            return self.scenario.get_command_script_status()

        if len(parts) == 3 and parts[0] == "shelves" and parts[2] == "packages":
            try:
                return self.scenario.get_shelf_packages(parts[1])
            except Exception as exc:
                raise ApiRequestError(404, str(exc))

        raise ApiRequestError(404, f"Unknown endpoint: {self.path}")

    def _handle_post(self):
        parts = self._path_parts()
        payload = self._read_json()

        try:
            if parts == ["logs", "clear"]:
                return self.scenario.clear_logs()

            if parts == ["runtime", "start"]:
                return self.scenario.ensure_runtime_pump()

            if parts == ["reset"]:
                return self.scenario.enqueue_reset_scene()

            if parts == ["scripts", "start"]:
                return self.scenario.start_command_script(
                    script_path=payload.get("path") or payload.get("script_path"),
                    script=payload.get("script"),
                )

            if parts == ["scripts", "stop"]:
                return self.scenario.stop_command_script()

            if parts == ["arm", "pick_place"]:
                return self.scenario.enqueue_arm_pick_place(
                    package_name=payload.get("package_name"),
                    candidate_index=payload.get("candidate_index"),
                    target=payload.get("target"),
                    arm_name=payload.get("arm_name"),
                    pallet_name=payload.get("pallet_name"),
                    pallet_index=payload.get("pallet_index"),
                    pallet_offset=payload.get("pallet_offset") or payload.get("relative_target") or payload.get("relative_position"),
                )

            if len(parts) == 3 and parts[0] == "conveyors" and parts[2] == "packages":
                return self.scenario.enqueue_conveyor_packages(
                    conveyor_name=parts[1],
                    count=payload.get("count", payload.get("n", 1)),
                    package_prefix=payload.get("package_prefix"),
                    dimensions=payload.get("dimensions"),
                    spacing=payload.get("spacing", 0.45),
                    at=payload.get("at", "end"),
                    interval_seconds=payload.get("interval_seconds", 0.35),
                )

            if parts == ["shuttle", "pick_to_conveyor"]:
                return self.scenario.enqueue_shuttle_pick_to_conveyor(
                    shelf_index=payload.get("shelf_index"),
                    package_names=payload.get("package_names"),
                    package_positions=payload.get("package_positions"),
                    conveyor_name=payload.get("conveyor_name"),
                    shuttle_name=payload.get("shuttle_name"),
                    drop_spacing=payload.get("drop_spacing", 0.45),
                )

            if parts == ["transporter", "move_pallet"]:
                return self.scenario.enqueue_transporter_move_pallet(
                    pallet_name=payload.get("pallet_name"),
                    pallet_index=payload.get("pallet_index"),
                    target=payload.get("target"),
                    transporter_name=payload.get("transporter_name"),
                )
        except Exception as exc:
            raise ApiRequestError(400, str(exc))

        raise ApiRequestError(404, f"Unknown endpoint: {self.path}")

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._request_json_payload = {}
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiRequestError(400, f"Invalid JSON: {exc}")
        if data is None:
            self._request_json_payload = {}
            return {}
        if not isinstance(data, dict):
            raise ApiRequestError(400, "JSON body must be an object.")
        self._request_json_payload = data
        return data

    def _path_parts(self):
        path = urlparse(self.path).path
        return [unquote(part) for part in path.strip("/").split("/") if part]

    def _query_params(self):
        from urllib.parse import parse_qs

        return parse_qs(urlparse(self.path).query)

    def _send_json(self, status_code, payload):
        body = b""
        if status_code != 204:
            body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_html(self, status_code, html):
        body = html.encode("utf-8")

        self.send_response(status_code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _api_index(self):
        return {
            "name": "LPB Simulator API",
            "version": "0.1.0",
            "note": "POST commands enqueue simulation tasks. Press RUN in Isaac Sim so queued motion tasks can progress.",
            "endpoints": [
                "GET /health",
                "GET /console",
                "GET /state",
                "GET /debug/snapshot",
                "GET /runtime",
                "GET /logs",
                "GET /tasks/{task_id}",
                "GET /candidate_pool",
                "GET /scripts/status",
                "POST /runtime/start",
                "POST /logs/clear",
                "POST /scripts/start",
                "POST /scripts/stop",
                "POST /reset",
                "POST /arm/pick_place",
                "POST /conveyors/{conveyor_name}/packages",
                "GET /shelves/{shelf_index}/packages",
                "POST /shuttle/pick_to_conveyor",
                "POST /transporter/move_pallet",
            ],
        }

    def _log_http_request(self, started_at, status_code, response_payload):
        if self.scenario is None:
            return
        path = urlparse(self.path).path
        if path in ("/logs", "/console") or path.startswith("/logs?"):
            return
        try:
            self.scenario.add_log(
                "DEBUG",
                "HTTP request handled",
                {
                    "method": self.command,
                    "path": self.path,
                    "status_code": int(status_code),
                    "duration_ms": round((time.time() - started_at) * 1000.0, 2),
                    "request": getattr(self, "_request_json_payload", None),
                    "response": _response_summary(response_payload),
                },
            )
        except Exception:
            pass


def _response_summary(payload):
    if isinstance(payload, HtmlResponse):
        return {"type": "html"}
    if not isinstance(payload, dict):
        return {"type": type(payload).__name__}
    summary = {"keys": sorted(str(key) for key in payload.keys())}
    for key in ("task_id", "kind", "status", "error", "running", "ok"):
        if key in payload:
            summary[key] = payload.get(key)
    if "candidate_pool" in payload and isinstance(payload.get("candidate_pool"), list):
        summary["candidate_count"] = len(payload.get("candidate_pool") or [])
    if "entries" in payload and isinstance(payload.get("entries"), list):
        summary["log_count"] = len(payload.get("entries") or [])
    if "active_task_id" in payload:
        summary["active_task_id"] = payload.get("active_task_id")
    if "queued_task_ids" in payload:
        summary["queued_task_count"] = len(payload.get("queued_task_ids") or [])
    return summary


def _json_default(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def create_server(scenario, host="0.0.0.0", port=60124):
    class BoundLPBApiHandler(LPBApiHandler):
        pass

    BoundLPBApiHandler.scenario = scenario
    return LPBThreadingHTTPServer((host, int(port)), BoundLPBApiHandler)
