#!/usr/bin/env python3
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


class ApiError(Exception):
    pass


class LPBApiClient:
    def __init__(self, base_url, timeout):
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, payload):
        return self.request("POST", path, payload)

    def request(self, method, path, payload=None):
        url = self.base_url + path
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
                data = json.loads(text) if text else {}
                return resp.status, data
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(text) if text else {}
            except json.JSONDecodeError:
                data = {"detail": text}
            raise ApiError(f"{method} {path} failed: HTTP {exc.code}: {data.get('detail', data)}")
        except urllib.error.URLError as exc:
            raise ApiError(f"{method} {path} failed: {exc}")


def parse_vec3(text):
    if isinstance(text, (list, tuple)):
        values = list(text)
    else:
        stripped = str(text).strip()
        if stripped.startswith("["):
            values = json.loads(stripped)
        else:
            values = [part.strip() for part in stripped.split(",")]
    if len(values) == 2:
        values.append(0)
    if len(values) != 3:
        raise argparse.ArgumentTypeError("Expected x,y,z or [x,y,z].")
    return [float(values[0]), float(values[1]), float(values[2])]


def print_step(title):
    print("")
    print("=" * 72)
    print(title)
    print("=" * 72)


def print_json(label, data):
    print(f"{label}:")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def wait_for_task(client, task_id, timeout, interval):
    deadline = time.time() + float(timeout)
    last_status = None
    while True:
        _, task = client.get(f"/tasks/{task_id}")
        status = task.get("status")
        if status != last_status:
            print(f"task {task_id}: {status} ({task.get('current_step')}/{task.get('total_steps')})")
            last_status = status
        if status in ("done", "failed"):
            return task
        if time.time() >= deadline:
            raise ApiError(f"task {task_id} did not finish within {timeout} seconds; latest status={status}")
        time.sleep(float(interval))


def submit_task(client, title, path, payload, wait, task_timeout, poll_interval):
    print_step(title)
    print_json("request", payload)
    _, response = client.post(path, payload)
    print_json("response", response)
    task_id = response.get("task_id")
    if task_id:
        _, task_snapshot = client.get(f"/tasks/{task_id}")
        print_json("task_snapshot", task_snapshot)
        if wait:
            final_task = wait_for_task(client, task_id, task_timeout, poll_interval)
            print_json("task_final", final_task)
            if final_task.get("status") == "failed":
                raise ApiError(f"task {task_id} failed: {final_task.get('error')}")
    return response


def first_shelf_package(client, shelf_index):
    _, shelf = client.get(f"/shelves/{shelf_index}/packages")
    print_json("shelf_packages", shelf)
    packages = shelf.get("packages") or []
    return packages[0].get("name") if packages else None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Send a fixed LPB API command sequence.")
    parser.add_argument("--base-url", default="http://localhost:60124")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP request timeout in seconds.")
    parser.add_argument("--wait", action="store_true", help="Poll each task until done/failed.")
    parser.add_argument("--task-timeout", type=float, default=120.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)

    parser.add_argument("--conveyor-name", default="DemoConveyor")
    parser.add_argument("--include-conveyor-spawn", action="store_true", help="Also test direct package spawning on the conveyor.")
    parser.add_argument("--package-count", type=int, default=1)
    parser.add_argument("--package-prefix", default=None)
    parser.add_argument("--package-dimensions", type=parse_vec3, default=[0.3, 0.3, 0.5])

    parser.add_argument("--arm-name", default="pallet_arm1")
    parser.add_argument("--candidate-index", type=int, default=1)
    parser.add_argument("--arm-target", type=parse_vec3, default=[8.2, 4.0, 0.8])

    parser.add_argument("--shelf-index", type=int, default=1)
    parser.add_argument("--shuttle-name", default="shuttle1")
    parser.add_argument("--shuttle-package-name", default="ShelfPkg_1")

    parser.add_argument("--pallet-index", type=int, default=1)
    parser.add_argument("--transporter-name", default="transporter1")
    parser.add_argument("--transporter-target", type=parse_vec3, default=[11.0, 4.0, 0.0])

    args = parser.parse_args(argv)
    client = LPBApiClient(args.base_url, args.timeout)
    package_prefix = args.package_prefix or f"ApiSmokeBox_{int(time.time())}"

    try:
        print_step("1. health")
        _, health = client.get("/health")
        print_json("health", health)

        print_step("2. state")
        _, state = client.get("/state")
        print_json("state", state)

        has_candidate_source = False
        if args.include_conveyor_spawn:
            submit_task(
                client,
                "3. optional conveyor packages -> candidate pool",
                f"/conveyors/{args.conveyor_name}/packages",
                {
                    "count": args.package_count,
                    "package_prefix": package_prefix,
                    "dimensions": args.package_dimensions,
                    "spacing": 0.45,
                    "at": "end",
                },
                args.wait,
                args.task_timeout,
                args.poll_interval,
            )
            has_candidate_source = True

        print_step("3. shelf packages")
        shelf_package = args.shuttle_package_name or first_shelf_package(client, args.shelf_index)
        if not shelf_package:
            print(f"No package found on shelf {args.shelf_index}; skip shuttle pick_to_conveyor.")
        else:
            submit_task(
                client,
                "4. shuttle shelf package -> conveyor end",
                "/shuttle/pick_to_conveyor",
                {
                    "shelf_index": args.shelf_index,
                    "package_names": [shelf_package],
                    "conveyor_name": args.conveyor_name,
                    "shuttle_name": args.shuttle_name,
                    "drop_spacing": 0.45,
                },
                args.wait,
                args.task_timeout,
                args.poll_interval,
            )
            has_candidate_source = True

        print_step("5. candidate pool")
        _, candidate_pool = client.get("/candidate_pool")
        print_json("candidate_pool", candidate_pool)

        if has_candidate_source:
            submit_task(
                client,
                "6. arm pick conveyor candidate -> pallet",
                "/arm/pick_place",
                {
                    "candidate_index": args.candidate_index,
                    "target": args.arm_target,
                    "arm_name": args.arm_name,
                },
                args.wait,
                args.task_timeout,
                args.poll_interval,
            )
        else:
            print("No candidate-producing step was submitted; skip arm pick_place.")

        submit_task(
            client,
            "7. transporter pallet -> outbound point",
            "/transporter/move_pallet",
            {
                "pallet_index": args.pallet_index,
                "target": args.transporter_target,
                "transporter_name": args.transporter_name,
            },
            args.wait,
            args.task_timeout,
            args.poll_interval,
        )

        print_step("8. final state")
        _, final_state = client.get("/state")
        print_json("state", final_state)

        print("")
        print("Sequence submitted successfully.")
        if not args.wait:
            print("Use --wait while Isaac Sim is RUNNING to verify task completion, not only enqueue success.")
        return 0
    except ApiError as exc:
        print("")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
