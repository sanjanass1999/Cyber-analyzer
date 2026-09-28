import asyncio
import os
import tempfile
from typing import List

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from context import (
    SECURITY_RESEARCHER_INSTRUCTIONS,
    enhance_summary,
    get_analysis_prompt,
)

load_dotenv(override=True)

app = FastAPI(title="Cybersecurity Analyzer API")

cors_origins = [
    "http://localhost:3000",
    "http://frontend:3000",
]

if os.getenv("ENVIRONMENT") == "production":
    cors_origins.append("*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    code: str


class SecurityIssue(BaseModel):
    title: str = Field(description="Brief title of the security vulnerability")
    description: str = Field(
        description="Detailed description of the security issue and its potential impact"
    )
    code: str = Field(
        description="The specific vulnerable code snippet that demonstrates the issue"
    )
    fix: str = Field(description="Recommended code fix or mitigation strategy")
    cvss_score: float = Field(
        description="CVSS score from 0.0 to 10.0 representing severity"
    )
    severity: str = Field(description="Severity level: critical, high, medium, or low")


class SecurityReport(BaseModel):
    summary: str = Field(description="Executive summary of the security analysis")
    issues: List[SecurityIssue] = Field(
        description="List of identified security vulnerabilities"
    )


def validate_request(request: AnalyzeRequest) -> None:
    if not request.code.strip():
        raise HTTPException(status_code=400, detail="No code provided for analysis")


def check_api_keys() -> None:
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(
            status_code=500, detail="GEMINI_API_KEY is not configured in .env"
        )


async def run_security_analysis(code: str) -> SecurityReport:
    """Execute analysis with retries and fallback across free-tier models."""
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as temp:
        temp.write(code)
        temp_path = temp.name

    # Priority ordered: stable workhorse models first, then preview/lite variants
    models_to_try = [
        "gemini-2.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
    ]

    try:
        user_prompt = get_analysis_prompt(code, temp_path)
        full_prompt = f"{SECURITY_RESEARCHER_INSTRUCTIONS}\n\nTask:\n{user_prompt}"

        last_exception = None

        for model_name in models_to_try:
            # Retry up to 2 times per model if a 503 spike occurs
            for attempt in range(2):
                try:
                    print(f"--> Sending request to {model_name} (attempt {attempt + 1})...")
                    response = client.models.generate_content(
                        model=model_name,
                        contents=full_prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=SecurityReport,
                            temperature=0.2,
                        ),
                    )
                    parsed_report = SecurityReport.model_validate_json(response.text)
                    parsed_report.issues.sort(
                        key=lambda issue: issue.cvss_score, reverse=True
                    )
                    print(f"--> Analysis completed successfully with {model_name}!")
                    return parsed_report

                except Exception as err:
                    err_msg = str(err)
                    last_exception = err
                    print(f"--> {model_name} attempt {attempt + 1} failed: {err_msg}")

                    # If temporary high demand, wait briefly before retrying
                    if "503" in err_msg or "UNAVAILABLE" in err_msg:
                        await asyncio.sleep(2.0)
                        continue
                    # If model not found or another error, switch immediately to the next model
                    break

        raise last_exception

    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def format_analysis_response(code: str, report: SecurityReport) -> SecurityReport:
    enhanced_summary = enhance_summary(len(code), report.summary)
    return SecurityReport(summary=enhanced_summary, issues=report.issues)


@app.post("/api/analyze", response_model=SecurityReport)
async def analyze_code(request: AnalyzeRequest) -> SecurityReport:
    validate_request(request)
    check_api_keys()

    try:
        report = await run_security_analysis(request.code)
        return format_analysis_response(request.code, report)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@app.get("/health")
async def health():
    return {"message": "Cybersecurity Analyzer API"}


if os.path.exists("static"):
    app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)