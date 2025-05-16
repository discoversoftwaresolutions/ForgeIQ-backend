from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class DiagnoseRequest(BaseModel):
    error_log: str

@app.post("/analyze")
async def analyze(request: DiagnoseRequest):
    if "ReferenceError" in request.error_log:
        return {"diagnosis": "Undefined variable", "confidence": 0.92}
    return {"diagnosis": "Generic error", "confidence": 0.65}
