"""
Rapidpower Robot Dog Web Control Server

FastAPI backend serving a control dashboard for the Rapidpower robot dog.
Reconstructed from protocol analysis for local control of operator's own device.

Run with: uvicorn robodog.server:app --host 0.0.0.0 --port 8770
Or with simulation: python -m robodog.server --simulate
"""

import asyncio
import logging
import argparse
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .controller import RobodogBLE
from .protocol import COMMANDS, FEED_COMMANDS, FRONT_LEG_ANGLES, BACK_LEG_ANGLES, command_names

VALID_COMMANDS = set(command_names())  # actions + inferred moves + entertainment + aliases

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI(
    title="Rapidpower Robot Dog Controller",
    description="Web dashboard for BLE control of Rapidpower robot dog",
    version="0.1.0"
)

# Global controller instance
controller: Optional[RobodogBLE] = None

# WebSocket clients for telemetry streaming
telemetry_clients: set[WebSocket] = set()


# Request/Response models
class ScanResponse(BaseModel):
    devices: list[Dict[str, Any]]


class ConnectRequest(BaseModel):
    address: str


class StatusResponse(BaseModel):
    connected: bool
    address: Optional[str]
    simulate: bool
    last_telemetry: Optional[str]


class CommandRequest(BaseModel):
    name: str


class LegsRequest(BaseModel):
    fl: int  # front left angle
    fr: int  # front right angle
    bl: int  # back left angle
    br: int  # back right angle


# Telemetry callback
async def broadcast_telemetry(hex_data: str):
    """Broadcast telemetry to all connected WebSocket clients."""
    if not telemetry_clients:
        return

    message = {"type": "telemetry", "data": hex_data}
    disconnected = set()

    for client in telemetry_clients:
        try:
            await client.send_json(message)
        except Exception as e:
            logger.warning(f"Failed to send telemetry to client: {e}")
            disconnected.add(client)

    # Remove disconnected clients
    for client in disconnected:
        telemetry_clients.discard(client)


# API Endpoints

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the control dashboard (robodog/static/dashboard.html)."""
    import os as _os
    p = _os.path.join(_os.path.dirname(__file__), "static", "dashboard.html")
    with open(p, encoding="utf-8") as fh:
        return HTMLResponse(fh.read())


@app.post("/api/scan", response_model=ScanResponse)
async def scan_devices(all: bool = False):
    """
    Scan for Rapidpower-dog BLE devices.

    Query params:
        all: if true, return EVERY advertising BLE device (unfiltered) so the
             operator can find the dog by whatever name it actually advertises.

    Returns:
        List of discovered devices with address, name, rssi, likely_dog
    """
    global controller
    if not controller:
        raise HTTPException(status_code=500, detail="Controller not initialized")

    try:
        devices = await controller.scan(include_all=all)
        logger.info(f"Scan found {len(devices)} device(s)")
        return ScanResponse(devices=devices)
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/connect")
async def connect_device(request: ConnectRequest = None):
    """
    Connect to robot dog.

    If no address is provided in the request, auto-scans and connects to first device.

    Args:
        request: Optional address to connect to
    """
    global controller
    if not controller:
        raise HTTPException(status_code=500, detail="Controller not initialized")

    try:
        address = request.address if request else None
        await controller.connect(address=address)
        logger.info(f"Connected to {controller.device_address}")
        return {"status": "connected", "address": controller.device_address}
    except Exception as e:
        logger.error(f"Connection failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/disconnect")
async def disconnect_device():
    """Disconnect from robot dog."""
    global controller
    if not controller:
        raise HTTPException(status_code=500, detail="Controller not initialized")

    try:
        await controller.disconnect()
        logger.info("Disconnected")
        return {"status": "disconnected"}
    except Exception as e:
        logger.error(f"Disconnect failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/status", response_model=StatusResponse)
async def get_status():
    """Get current connection status and last telemetry."""
    global controller
    if not controller:
        raise HTTPException(status_code=500, detail="Controller not initialized")

    status = controller.get_status()
    return StatusResponse(**status)


@app.post("/api/command/{name}")
async def send_command(name: str):
    """
    Send a command to the robot.

    Args:
        name: Command name (see COMMANDS dict for valid names)
    """
    global controller
    if not controller:
        raise HTTPException(status_code=500, detail="Controller not initialized")

    if not controller.is_connected:
        raise HTTPException(status_code=400, detail="Not connected to device")

    if name not in VALID_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Unknown command: {name}")

    try:
        await controller.send_command(name)
        logger.info(f"Sent command: {name}")
        return {"status": "sent", "command": name}
    except Exception as e:
        logger.error(f"Command send failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/feed/{name}")
async def send_feed(name: str):
    """
    Send a feed command to the robot.

    Args:
        name: Feed command name (see FEED_COMMANDS dict)
    """
    global controller
    if not controller:
        raise HTTPException(status_code=500, detail="Controller not initialized")

    if not controller.is_connected:
        raise HTTPException(status_code=400, detail="Not connected to device")

    if name not in FEED_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Unknown feed command: {name}")

    try:
        await controller.send_feed(name)
        logger.info(f"Sent feed command: {name}")
        return {"status": "sent", "feed": name}
    except Exception as e:
        logger.error(f"Feed command failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/legs")
async def send_legs(request: LegsRequest):
    """
    Send individual leg position control.

    Args:
        request: Leg angles for fl, fr, bl, br
    """
    global controller
    if not controller:
        raise HTTPException(status_code=500, detail="Controller not initialized")

    if not controller.is_connected:
        raise HTTPException(status_code=400, detail="Not connected to device")

    try:
        await controller.send_legs(request.fl, request.fr, request.bl, request.br)
        logger.info(f"Sent leg positions: FL={request.fl}, FR={request.fr}, BL={request.bl}, BR={request.br}")
        return {"status": "sent", "legs": {"fl": request.fl, "fr": request.fr, "bl": request.bl, "br": request.br}}
    except Exception as e:
        logger.error(f"Leg command failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/telemetry")
async def telemetry_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for streaming telemetry data.

    Clients receive JSON messages: {"type": "telemetry", "data": "<hex>"}
    """
    await websocket.accept()
    telemetry_clients.add(websocket)
    logger.info(f"Telemetry WebSocket connected (total clients: {len(telemetry_clients)})")

    try:
        # Keep connection alive and listen for client messages
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        telemetry_clients.discard(websocket)
        logger.info(f"Telemetry WebSocket disconnected (remaining: {len(telemetry_clients)})")


@app.get("/api/commands")
async def list_commands():
    """List all available commands."""
    return {
        "commands": command_names(),
        "feed": list(FEED_COMMANDS.keys()),
        "leg_angles": {
            "front": list(set(FRONT_LEG_ANGLES.keys())),
            "back": list(set(BACK_LEG_ANGLES.keys()))
        }
    }


@app.on_event("startup")
async def startup_event():
    """Initialize controller on startup."""
    global controller
    # Controller will be set by main() with --simulate flag
    if controller is None:
        controller = RobodogBLE(simulate=False)
        logger.info("Controller initialized (real BLE mode)")

    # Set telemetry callback
    controller.set_notify_callback(lambda hex_data: asyncio.create_task(broadcast_telemetry(hex_data)))


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up on shutdown."""
    global controller
    if controller and controller.is_connected:
        await controller.disconnect()
    logger.info("Server shutdown")


def main():
    """Run server with command-line argument support."""
    parser = argparse.ArgumentParser(description="Rapidpower Robot Dog Control Server")
    parser.add_argument("--simulate", action="store_true", help="Run in simulation mode (no real BLE device)")
    parser.add_argument("--host", default="0.0.0.0", help="Server host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8770, help="Server port (default: 8770)")
    args = parser.parse_args()

    # Set global controller with simulation flag
    global controller
    controller = RobodogBLE(simulate=args.simulate)

    if args.simulate:
        logger.info("=" * 50)
        logger.info("SIMULATION MODE ENABLED")
        logger.info("No real BLE device required")
        logger.info("=" * 50)

    # Run with uvicorn
    import uvicorn
    uvicorn.run(
        "robodog.server:app",
        host=args.host,
        port=args.port,
        log_level="info",
        reload=False
    )


if __name__ == "__main__":
    main()
