#!/usr/bin/env python3
"""
linkedinHunter is a reconnaissance-oriented OSINT utility that leverages the Google
Custom Search API to discover public LinkedIn profiles associated with a given
organization.

The tool extracts individual names from search results and, when provided with
a naming pattern, infers potential corporate email addresses. Results are
deduplicated, enriched, and exported in structured JSON format alongside
execution metrics for cost and usage tracking.

This tool does not scrape LinkedIn directly and relies exclusively on publicly
indexed data available through Google Search.
"""

import argparse
import requests
import json
import time
import re
import sys
import unicodedata
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from datetime import datetime

GOOGLE_API_URL = "https://www.googleapis.com/customsearch/v1"
REQUEST_DELAY = 1.0

"""
Coverage US
"""
"""
COMMON_ROLES = [
    "",
    "IT", "Human Resources", "Recruiter", "Marketing", "Finance",
    "Sales", "Director", "Manager", "Developer", "Engineer",
    "Consultant", "Analyst", "CEO", "CTO", "CISO", "Administrator",
    "Management", "Legal", "Support", "HR", "Health", "Operations"
]
"""

"""
Coverage ES
"""
COMMON_ROLES = [
    "", 
    
    "Gestor", "Gestor Comercial", "Director de Oficina", "Subdirector",
    "Interventor", "Cajero", "Atención al Cliente", "Asesor Financiero",
    "Banca Privada", "Banca Personal", "Banca de Empresas", "Gerente",
    "Territorial", "Zona", 
    
    "Analista", "Riesgos", "Admisiones", "Auditoría", "Control",
    "Recursos Humanos", "Talento", "Selección", "Formación",
    "Marketing", "Comunicación", "Prensa", "Marca",
    "Legal", "Jurídico", "Compliance", "Normativa", "Abogado",
    "Contabilidad", "Financiero", "Tesoreria", "Fiscal",
    "Operaciones", "Administrativo", "Secretaría",
    
    "Informática", "Sistemas", "Tecnología", "Desarrollador", "Programador",
    "Arquitecto", "Ingeniero", "Ciberseguridad", "Seguridad", "CISO",
    "Datos", "Data", "Analítica", "Big Data", "Transformación",
    "Digital", "Innovation", "Innovación", "Agile", "Scrum",
    "Soporte", "Helpdesk", "Técnico",
    
    "Director", "Responsable", "Jefe", "Coordinador", "Manager",
    "Delegado", "Presidente", "Consejero", "Socio", 
    "CEO", "CTO", "CIO", "CFO", "COO" 
]

@dataclass
class Employee:
    """
    Represents a single LinkedIn profile identified during the harvest process.
    """
    name: str
    linkedin_url: str
    role_snippet: str
    generated_emails: List[str] = None


class LinkedInHarvester:
    """
    Core engine responsible for querying Google CSE, extracting identities,
    inferring emails, and maintaining deduplicated results.
    """

    def __init__(self, api_key: str, cse_id: str, organization: str, output_file: str, email_format: Optional[str] = None):
        self.api_key = api_key
        self.cse_id = cse_id
        self.organization = organization
        self.output_file = output_file 
        self.email_format = email_format
        self.found_employees: Dict[str, Employee] = {}
        self.session = requests.Session()
        self.total_requests = 0
        self.start_time = time.time()
        
        self.save_results()

    def _remove_accents(self, value: str) -> str:
        """
        Normalizes Unicode characters to ASCII by removing diacritics.
        """
        if not value:
            return ""
        normalized = unicodedata.normalize("NFKD", value)
        return "".join(c for c in normalized if not unicodedata.combining(c))

    def _clean_name(self, title: str) -> str:
        """
        Extracts a person's name from a Google Search result title.
        """
        cleaned = re.sub(r"\s?[|\-]\s?LinkedIn.*$", "", title, flags=re.IGNORECASE)
        parts = re.split(r"\s[–-]\s", cleaned)
        return parts[0].strip()

    def _generate_emails(self, full_name: str) -> Optional[str]:
        """
        Generates a list of potential email addresses based on Spanish naming conventions.
        Strategy:
          - If 2 words (Name Surname): {last} uses Surname.
          - If 3+ words (Name Surname1 Surname2 OR Name1 Name2 Surname1...):
            Generates emails using the 2nd word and the 3rd word to maximize hit rate.
        """

        if not self.email_format:
            return []

        normalized = self._remove_accents(full_name).lower()
        normalized = re.sub(r"[^a-z0-9\s]", "", normalized)
        name_parts = normalized.split()

        if len(name_parts) < 2:
            return []

        first_name = name_parts[0]
        first_initial = first_name[0]

        candidate_surnames = []

        if len(name_parts) == 2:
            candidate_surnames.append(name_parts[1])
        else:
            candidate_surnames.append(name_parts[1])
            candidate_surnames.append(name_parts[2])

        generated_list = []
        
        for last in candidate_surnames:
            if not last: 
                continue
                
            email = self.email_format
            email = email.replace("{first}", first_name)
            email = email.replace("{last}", last)
            email = email.replace("{f}", first_initial)
            email = email.replace("{l}", last[0])
            
            if email not in generated_list:
                generated_list.append(email)

        return generated_list

    def search_google(self, query: str, start_index: int) -> List[Dict]:
        """
        Executes a Google Custom Search API request and returns raw result items.
        """
        params = {
            "key": self.api_key,
            "cx": self.cse_id,
            "q": query,
            "num": 10,
            "start": start_index
        }

        time.sleep(REQUEST_DELAY)
        self.total_requests += 1

        try:
            response = self.session.get(GOOGLE_API_URL, params=params)
            if response.status_code == 429:
                time.sleep(10)
                return []

            response.raise_for_status()
            return response.json().get("items", [])

        except Exception as exc:
            print(f"[!] Request error: {exc}", file=sys.stderr)
            return []

    def harvest(self):
        """
        Iterates through role-based queries to maximize discovery coverage.
        """
        print(f"[*] Target organization: {self.organization}")
        print(f"[*] Email inference enabled: {'Yes' if self.email_format else 'No'}")

        for role in COMMON_ROLES:
            query = f"\"{self.organization}\" {role}".strip()
            print(f"\n[>] Query: {query}")

            for page in range(10):
                start = (page * 10) + 1
                results = self.search_google(query, start)

                if not results:
                    break

                for item in results:
                    link = item.get("link", "")
                    if link in self.found_employees:
                        continue

                    name = self._clean_name(item.get("title", ""))
                    if len(name) > 60 or "profiles" in name.lower():
                        continue

                    employee = Employee(
                        name=name,
                        linkedin_url=link,
                        role_snippet=item.get("snippet", "").replace("\n", " "),
                        generated_emails=self._generate_emails(name) 
                    )

                    self.found_employees[link] = employee
                    emails_str = ", ".join(employee.generated_emails) if employee.generated_emails else "N/A"
                    print(f"    + {name} -> [{emails_str}]")

                    self.save_results()

    def save_results(self):
        """
        Persists discovered profiles to disk in JSON format.
        """
        with open(self.output_file, "w", encoding="utf-8") as fh:
            json.dump([asdict(e) for e in self.found_employees.values()], fh, indent=4, ensure_ascii=False)

    def save_metrics(self, filename: str):
        """
        Saves execution metrics for auditability and API cost estimation.
        """
        metrics = {
            "organization": self.organization,
            "timestamp": datetime.utcnow().isoformat(),
            "api_requests": self.total_requests,
            "profiles_found": len(self.found_employees),
            "execution_time_seconds": round(time.time() - self.start_time, 2)
        }

        with open(filename, "w", encoding="utf-8") as fh:
            json.dump(metrics, fh, indent=4)


def main():
    parser = argparse.ArgumentParser(description="OSINT LinkedIn profile discovery using Google CSE")

    parser.add_argument("--api-key", required=True)
    parser.add_argument("--cse-id", required=True)
    parser.add_argument("--org", required=True)
    parser.add_argument("--email-format")
    parser.add_argument("--output", default="employees.json")
    parser.add_argument("--metrics", default="metrics.json")

    args = parser.parse_args()

    harvester = LinkedInHarvester(
        api_key=args.api_key,
        cse_id=args.cse_id,
        organization=args.org,
        output_file=args.output,
        email_format=args.email_format
    )

    try:
        harvester.harvest()
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user")
    finally:
        harvester.save_metrics(args.metrics)


if __name__ == "__main__":
    main()
