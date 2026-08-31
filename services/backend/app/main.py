from fastapi import FastAPI

app = FastAPI(title="AI Assistant Backend")

@app.get("/health")
def health_check():
    return {"status": "ok"}
