# python_services/app/main.py
from fastapi import FastAPI
from . import routes # Import the routes module

app = FastAPI(
    title="Flashbook Trading System API",
    description="API for submitting orders and getting market data.",
    version="0.1.0",
)

# Include the API routes defined in routes.py
app.include_router(routes.router, prefix="/api/v1") # Add a version prefix

@app.get("/")
async def read_root():
    """ Basic health check endpoint. """
    return {"message": "Flashbook API is running"}

# Optional: Add startup/shutdown events later if needed
# @app.on_event("startup")
# async def startup_event():
#     print("Application startup...")
#
# @app.on_event("shutdown")
# async def shutdown_event():
#     print("Application shutdown...")