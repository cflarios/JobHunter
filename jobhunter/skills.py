"""Extracción de skills técnicas de un texto (título + descripción + tags).

Diccionario curado de tecnologías; matching por palabra completa (delimitada por
caracteres no alfanuméricos), case-insensitive, con alias (k8s→Kubernetes,
google cloud→GCP…). Devuelve una lista ordenada por relevancia (orden del
diccionario) y sin duplicados, acotada. No depende de nada del proyecto.
"""
import re

# (canónico, [alias/variantes en minúsculas]). Orden ≈ relevancia para DevOps/SRE
# y software en general. Se evitan palabras ambiguas (go→golang, rest→rest api…).
_SKILLS = [
    ("Kubernetes", ["kubernetes", "k8s"]),
    ("Docker", ["docker"]),
    ("Terraform", ["terraform"]),
    ("AWS", ["aws", "amazon web services"]),
    ("Azure", ["azure"]),
    ("GCP", ["gcp", "google cloud platform", "google cloud"]),
    ("Ansible", ["ansible"]),
    ("Helm", ["helm"]),
    ("Pulumi", ["pulumi"]),
    ("CloudFormation", ["cloudformation", "cloud formation"]),
    ("OpenShift", ["openshift"]),
    ("CI/CD", ["ci/cd", "cicd", "ci cd"]),
    ("Jenkins", ["jenkins"]),
    ("GitLab CI", ["gitlab ci", "gitlab-ci"]),
    ("GitHub Actions", ["github actions"]),
    ("ArgoCD", ["argocd", "argo cd"]),
    ("CircleCI", ["circleci", "circle ci"]),
    ("Prometheus", ["prometheus"]),
    ("Grafana", ["grafana"]),
    ("Datadog", ["datadog", "data dog"]),
    ("ELK", ["elk stack", "elk"]),
    ("Splunk", ["splunk"]),
    ("OpenTelemetry", ["opentelemetry", "open telemetry"]),
    ("Linux", ["linux"]),
    ("Bash", ["bash", "shell scripting"]),
    ("Git", ["git"]),
    ("Nginx", ["nginx"]),
    ("Kafka", ["kafka"]),
    ("Redis", ["redis"]),
    ("Elasticsearch", ["elasticsearch"]),
    ("PostgreSQL", ["postgresql", "postgres"]),
    ("MySQL", ["mysql"]),
    ("MongoDB", ["mongodb", "mongo"]),
    ("SQL", ["sql"]),
    ("Spark", ["spark"]),
    ("Airflow", ["airflow"]),
    ("Snowflake", ["snowflake"]),
    ("Python", ["python"]),
    ("Go", ["golang", "go lang"]),
    ("Java", ["java"]),
    ("JavaScript", ["javascript"]),
    ("TypeScript", ["typescript"]),
    ("Node.js", ["node.js", "nodejs", "node js"]),
    ("Ruby", ["ruby"]),
    ("Rust", ["rust"]),
    ("C++", ["c++"]),
    ("C#", ["c#"]),
    ("PHP", ["php"]),
    ("Scala", ["scala"]),
    ("Kotlin", ["kotlin"]),
    (".NET", [".net", "dotnet"]),
    ("React", ["react"]),
    ("Angular", ["angular"]),
    ("Vue", ["vue.js", "vuejs", "vue"]),
    ("Django", ["django"]),
    ("Flask", ["flask"]),
    ("Spring", ["spring boot", "spring"]),
    ("GraphQL", ["graphql"]),
    ("REST API", ["rest api", "restful", "rest apis"]),
    ("Microservices", ["microservices", "microservice"]),
    ("Serverless", ["serverless"]),
    ("Machine Learning", ["machine learning"]),
    ("DevOps", ["devops"]),
    ("SRE", ["sre", "site reliability"]),
    ("Agile", ["agile"]),
    ("Scrum", ["scrum"]),
]


# Selección curada para el filtro rápido de la página Empleos (chips de un clic).
# Es un subconjunto de _SKILLS ordenado por relevancia para DevOps/SRE; el usuario
# puede además escribir cualquier otra skill a mano.
QUICK_DEVOPS = [
    "Kubernetes", "Docker", "Terraform", "AWS", "Azure", "GCP", "Ansible", "Helm",
    "CI/CD", "Jenkins", "GitHub Actions", "GitLab CI", "ArgoCD", "Prometheus",
    "Grafana", "Datadog", "Linux", "Bash", "Python", "Go", "Kafka", "PostgreSQL",
    "Redis", "Nginx",
]


def _compile(alias):
    # Delimitado por algo que no sea alfanumérico (los símbolos +, #, ., / del propio
    # alias van escapados y forman parte del match).
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])", re.I)


_COMPILED = [(canon, [_compile(a) for a in aliases]) for canon, aliases in _SKILLS]


def extract_skills(text, limit=8):
    """Devuelve hasta `limit` skills canónicas encontradas en `text`, en orden de
    relevancia (según el diccionario). Lista vacía si no hay texto."""
    if not text:
        return []
    found = []
    for canon, patterns in _COMPILED:
        if any(p.search(text) for p in patterns):
            found.append(canon)
            if len(found) >= limit:
                break
    return found


def extract_skills_str(text, limit=8):
    """Como extract_skills pero devuelve una cadena separada por comas (para la BD)."""
    return ", ".join(extract_skills(text, limit))


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else \
        "Senior DevOps Engineer — Kubernetes, Terraform, AWS, Python, CI/CD, Go and Docker"
    print(extract_skills(t))
