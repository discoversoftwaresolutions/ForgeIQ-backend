from fastapi import Security, HTTPException

def get_api_key(api_key: str = Security(...)):  # Replace `...` with actual validation logic
    if api_key != "your-secure-api-key":  # ✅ Implement your API key validation mechanism
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return api_key
