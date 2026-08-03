from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, users, items, procedures, appointments, insights, imports, analytics

app = FastAPI(title="Mediventory API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "https://flourishing-quokka-858857.netlify.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(items.router)
app.include_router(procedures.router)
app.include_router(appointments.router)
app.include_router(insights.router)
app.include_router(imports.router)
app.include_router(analytics.router)

@app.get("/")
def read_root():
    return {"message": "Mediventory is alive"}