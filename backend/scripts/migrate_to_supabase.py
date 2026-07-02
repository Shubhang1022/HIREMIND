import os
import json
import uuid
import sys
from pathlib import Path

# Add backend directory to path
backend_dir = Path(__file__).resolve().parents[1]
sys.path.append(str(backend_dir))

from app.core.config import settings
from app.services.storage_provider import SupabaseStorageProvider, create_supabase_client
from supabase import Client

DATA_FILE = backend_dir.parent / "data" / "platform_data.json"
CANDIDATES_DIR = backend_dir.parent / "data" / "candidates"
EMBEDDINGS_DIR = backend_dir.parent / "data" / "embeddings"
INDEXES_DIR = backend_dir.parent / "data" / "indexes"
LOCK_FILE = backend_dir / "migration.lock"

def migrate():
    if LOCK_FILE.exists():
        print("Migration already completed. Lock file exists.")
        return

    url = settings.supabase_url or "https://okhxqdmajbibloxuhquy.supabase.co"
    key = settings.supabase_service_key or "sb_secret_FDTVjRiSs3kuGwlKoWtctQ_CFBm_MBV"
    
    print(f"Connecting to Supabase: {url}")
    client: Client = create_supabase_client(url, key)
    
    # Check if database has already been migrated
    try:
        hist = client.table("migration_history").select("*").eq("version", "v1.0").execute()
        if hist.data:
            print("Migration already completed in database (history table).")
            # Write local lock file for safety
            LOCK_FILE.write_text("migrated")
            return
    except Exception as exc:
        print(f"Error checking migration history (will attempt table check): {exc}")

    if not DATA_FILE.exists():
        print(f"No local data file found at {DATA_FILE}. Skipping legacy data migration.")
        # Mark history as completed anyway
        try:
            client.table("migration_history").insert({"version": "v1.0"}).execute()
            LOCK_FILE.write_text("migrated")
        except Exception as e:
            print(f"Could not write migration history: {e}")
        return

    print(f"Loading legacy data from {DATA_FILE}")
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    projects = data.get("projects", {})
    jobs = data.get("jobs", {})
    rankings = data.get("rankings", {})

    print(f"Found {len(projects)} projects, {len(jobs)} jobs, {len(rankings)} rankings.")

    storage = SupabaseStorageProvider()

    # 1. Migrate Projects
    for pid, p in projects.items():
        print(f"Migrating project {p.get('name')} ({pid})")
        user_id = p.get("user_id")
        try:
            uuid.UUID(user_id)
        except (ValueError, TypeError, AttributeError):
            user_id = "d6c20e10-8518-46b3-ba72-e88e77d2a912" # Fallback to local admin
        
        
        # Check if project exists
        existing = client.table("projects").select("id").eq("id", pid).execute()
        if not existing.data:
            # Prepare project row
            proj_row = {
                "id": pid,
                "user_id": user_id,
                "name": p.get("name"),
                "description": p.get("description"),
                "status": p.get("status") or "COMPLETED",
                "embedding_status": p.get("embedding_status") or "ready",
                "project_hash": p.get("project_hash"),
                "dataset_hash": p.get("dataset_hash"),
                "jd_hash": p.get("jd_hash"),
                "candidate_count": p.get("candidate_count", 0),
                "job_count": p.get("job_count", 0),
                "version": p.get("version", 1),
                "upload_statistics": p.get("upload_statistics", {}),
                "role_index_path": p.get("role_index_path"),
                "skill_index_path": p.get("skill_index_path"),
                "faiss_index_path": p.get("faiss_index_path"),
                "embeddings_path": p.get("embeddings_path"),
                "current_candidate_path": p.get("current_candidate_path"),
                "created_at": p.get("created_at"),
                "updated_at": p.get("updated_at")
            }
            client.table("projects").insert(proj_row).execute()
            print("Project inserted.")
        
        # Migrate candidate file to storage
        cand_file = CANDIDATES_DIR / f"{pid}_candidates.jsonl"
        if cand_file.exists():
            print(f"Uploading candidate file for project {pid}")
            file_size = cand_file.stat().st_size
            if file_size > 25 * 1024 * 1024:
                print(f"File is large ({file_size / (1024*1024):.1f} MB). Truncating to first 5000 lines to fit Supabase free tier storage limit.")
                lines = []
                with open(cand_file, "r", encoding="utf-8") as fh:
                    for line in fh:
                        lines.append(line)
                        if len(lines) >= 5000:
                            break
                content = "".join(lines).encode("utf-8")
            else:
                with open(cand_file, "rb") as fh:
                    content = fh.read()
            storage_path = f"{pid}/candidate_v1.jsonl"
            storage.upload_file("candidate-files", storage_path, content)
            
            # Insert into candidate_uploads
            client.table("candidate_uploads").insert({
                "project_id": pid,
                "storage_path": storage_path,
                "version": 1,
                "candidate_count": p.get("candidate_count", 0),
                "status": "COMPLETED"
            }).execute()
            
            # Update project candidate path
            client.table("projects").update({
                "current_candidate_path": f"candidate-files/{storage_path}"
            }).eq("id", pid).execute()

        # Migrate embeddings to storage
        emb_file = EMBEDDINGS_DIR / f"{pid}_embeddings.npy"
        if emb_file.exists():
            print(f"Uploading embeddings for project {pid}")
            with open(emb_file, "rb") as fh:
                content = fh.read()
            storage_path = f"{pid}/embeddings_v1.npy"
            storage.upload_file("embeddings", storage_path, content)
            client.table("projects").update({
                "embeddings_path": f"embeddings/{storage_path}"
            }).eq("id", pid).execute()

        # Migrate embeddings ids to storage
        ids_file = EMBEDDINGS_DIR / f"{pid}_ids.json"
        if ids_file.exists():
            print(f"Uploading embedding IDs for project {pid}")
            with open(ids_file, "rb") as fh:
                content = fh.read()
            storage_path = f"{pid}/ids_v1.json"
            storage.upload_file("embeddings", storage_path, content)

        # Migrate FAISS index to storage
        faiss_file = INDEXES_DIR / f"{pid}_faiss.index"
        if not faiss_file.exists():
            # Try .faiss extension
            faiss_file = INDEXES_DIR / f"{pid}_faiss.faiss"
        if faiss_file.exists():
            print(f"Uploading FAISS index for project {pid}")
            with open(faiss_file, "rb") as fh:
                content = fh.read()
            storage_path = f"{pid}/faiss_v1.index"
            storage.upload_file("faiss-indexes", storage_path, content)
            client.table("projects").update({
                "faiss_index_path": f"faiss-indexes/{storage_path}"
            }).eq("id", pid).execute()

        # Migrate Role Index JSON to storage
        role_file = INDEXES_DIR / f"{pid}_role_index.json"
        if role_file.exists():
            print(f"Uploading Role Index for project {pid}")
            with open(role_file, "rb") as fh:
                content = fh.read()
            storage_path = f"{pid}/role_index_v1.json"
            storage.upload_file("role-indexes", storage_path, content)
            client.table("projects").update({
                "role_index_path": f"role-indexes/{storage_path}"
            }).eq("id", pid).execute()

        # Migrate Skill Index JSON to storage
        skill_file = INDEXES_DIR / f"{pid}_skill_index.json"
        if skill_file.exists():
            print(f"Uploading Skill Index for project {pid}")
            with open(skill_file, "rb") as fh:
                content = fh.read()
            storage_path = f"{pid}/skill_index_v1.json"
            storage.upload_file("skill-indexes", storage_path, content)
            client.table("projects").update({
                "skill_index_path": f"skill-indexes/{storage_path}"
            }).eq("id", pid).execute()

    # 2. Migrate Jobs
    for jid, j in jobs.items():
        print(f"Migrating job {j.get('title')} ({jid})")
        existing = client.table("jobs").select("id").eq("id", jid).execute()
        if not existing.data:
            job_row = {
                "id": jid,
                "project_id": j.get("project_id"),
                "title": j.get("title"),
                "description": j.get("description"),
                "company": j.get("company"),
                "location": j.get("location"),
                "work_mode": j.get("work_mode") or "Onsite",
                "role_category": j.get("role_category"),
                "seniority": j.get("seniority"),
                "min_experience": j.get("min_experience") or j.get("experience_years", {}).get("min", 0),
                "required_skills": j.get("required_skills", []),
                "nice_to_have_skills": j.get("nice_to_have_skills", []),
                "preferred_locations": j.get("preferred_locations", []),
                "openings": j.get("openings", 5),
                "shortlist_size": j.get("shortlist_size", 15),
                "priority": j.get("priority") or "balanced",
                "min_match_percent": j.get("min_match_percent"),
                "salary_range": j.get("salary_range"),
                "job_location": j.get("job_location"),
                "employment_type": j.get("employment_type") or "Full-time",
                "created_at": j.get("created_at")
            }
            client.table("jobs").insert(job_row).execute()
            print("Job inserted.")

    # 3. Migrate Rankings & Results
    for rid, r in rankings.items():
        print(f"Migrating ranking {rid}")
        existing = client.table("rankings").select("id").eq("id", rid).execute()
        if not existing.data:
            # Prepare Ranking Row
            rank_row = {
                "id": rid,
                "project_id": r.get("project_id"),
                "job_id": r.get("job_id"),
                "version": r.get("version", 1),
                "status": r.get("status") or "completed",
                "total_candidates": r.get("total_candidates", 0),
                "ranked_count": r.get("ranked_count", 0),
                "dataset_hash": r.get("dataset_hash"),
                "jd_hash": r.get("jd_hash"),
                "version_metadata": r.get("version_metadata", {}),
                "metrics": r.get("metrics", {}),
                "prefilter_statistics": r.get("prefilter_statistics", {}),
                "metadata_only_fallback": r.get("metadata_only_fallback", False),
                "ai_enhancement_unavailable": r.get("ai_enhancement_unavailable", False),
                "created_at": r.get("created_at")
            }
            client.table("rankings").insert(rank_row).execute()
            print("Ranking inserted.")

            # Prepare Results Rows
            results = r.get("results", [])
            print(f"Inserting {len(results)} ranking results rows...")
            results_rows = []
            for res in results:
                results_rows.append({
                    "ranking_id": rid,
                    "candidate_id": res.get("candidate_id"),
                    "rank": res.get("rank"),
                    "score": res.get("ai_score") or res.get("score") or 0.0,
                    "reasoning": res.get("reasoning"),
                    "eligibility": res.get("eligibility", True),
                    "critical_skill_coverage": res.get("critical_skill_coverage"),
                    "full_result": res
                })
            
            # Batch insert results in chunks of 50 to avoid postgrest limits
            for i in range(0, len(results_rows), 50):
                client.table("ranking_results").insert(results_rows[i:i+50]).execute()
            print("Ranking results inserted.")

    # Record migration completion
    client.table("migration_history").insert({"version": "v1.0"}).execute()
    LOCK_FILE.write_text("migrated")
    print("Migration finished successfully.")

if __name__ == "__main__":
    migrate()
