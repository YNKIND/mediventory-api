from fastapi import FastAPI
from app.routers import auth, items, procedures, appointments, insights

app = FastAPI()

app.include_router(auth.router)


@app.get("/")
def read_root():
    return {"message": "Mediventory is alive"}


app = FastAPI()

app.include_router(auth.router)
app.include_router(items.router)


@app.get("/")
def read_root():
    return {"message": "Mediventory is alive"}


app = FastAPI()

app.include_router(auth.router)
app.include_router(items.router)
app.include_router(procedures.router)


@app.get("/")
def read_root():
    return {"message": "Mediventory is alive"}

app.include_router(appointments.router)
app.include_router(insights.router)