import json
from pathlib import Path
from typing import Dict, List


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


from app.config import ADVISOR_REPORT_FILE, COMMAND_STATS_FILE, ERROR_STATS_FILE



class OptimizationService:
    def __init__(self, logger=None):
        self.logger = logger or (lambda msg: None)

    def _log(self, msg: str) -> None:
        self.logger(msg)

    def _load_json(self, path: Path) -> Dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def build_recommendations(self) -> Dict:
        command_stats = self._load_json(COMMAND_STATS_FILE)
        error_stats = self._load_json(ERROR_STATS_FILE)

        top_commands = command_stats.get("top_commands", [])
        top_technologies = command_stats.get("top_technologies", [])
        top_intents = command_stats.get("top_intents", [])
        history_volume = command_stats.get("history_volume", {})
        source_breakdown = command_stats.get("source_breakdown", {})
        event_type_breakdown = command_stats.get("event_type_breakdown", {})
        top_errors = error_stats.get("top_errors", [])

        entries_cleaned = history_volume.get("entries_cleaned", 0)
        entries_raw = history_volume.get("entries_raw", 0)
        session_count = history_volume.get("session_count", 0)
        noise_ratio = history_volume.get("noise_ratio", 0)

        tech_names = {name for name, _count in top_technologies}
        intent_names = {name for name, _count in top_intents}

        recommendations: List[str] = []
        model_advice: List[str] = []
        mcp_advice: List[str] = []
        workflow_advice: List[str] = []
        warnings: List[str] = []

        app_events = event_type_breakdown.get("ui_action", 0) + event_type_breakdown.get("pipeline_result", 0)
        conversation_events = event_type_breakdown.get("conversation", 0)

        if entries_cleaned < 10:
            warnings.append("Historique encore faible : les recommandations restent indicatives.")
        elif entries_cleaned < 25:
            warnings.append("Historique encore limité : le profil devient utile mais pas encore totalement stable.")

        if session_count <= 1:
            warnings.append("Une seule session conversationnelle utile détectée : ton usage réel n’est probablement pas encore entièrement représenté.")

        if noise_ratio >= 0.8:
            warnings.append("Le ratio bruit / utile est élevé : l’historique contient encore beaucoup de doublons ou de commandes peu informatives.")

        if app_events > 0:
            recommendations.append("La collecte enrichie via l’application est active : plus tu utilises l’outil, plus les recommandations vont devenir fiables.")

        if conversation_events == 0:
            warnings.append("Aucune vraie conversation exploitable détectée : l’analyse repose presque uniquement sur l’app.")
        elif app_events < 3:
            warnings.append("Les traces applicatives restent encore peu nombreuses : utilise davantage les boutons de l’app pour enrichir le profil.")

        if "optimisation_claude" in intent_names:
            recommendations.append("Tu cherches explicitement à optimiser Claude : garde des sessions courtes et évite de charger des MCP non utilisés.")

        if "pilotage_ui" in intent_names:
            workflow_advice.append("Tu pilotes déjà ton environnement depuis l’app : garde ce flux centralisé pour améliorer la qualité des traces.")

        if "refactorisation" in intent_names:
            workflow_advice.append("Pour la refactorisation, demande un fichier complet corrigé quand tu veux aller vite, et un patch ciblé quand tu veux garder la maîtrise du diff.")

        if "suivi_modifs" in intent_names:
            workflow_advice.append("Pour suivre les changements, garde VS Code ouvert sur le bon dossier et demande des modifications fichier par fichier.")

        if "lancement_local" in intent_names:
            workflow_advice.append("Sépare les demandes de lancement local des demandes d’analyse pour éviter d’encombrer le contexte.")

        if "exploration_fichiers" in intent_names:
            workflow_advice.append("Quand tu explores une arborescence, demande d’abord une synthèse de structure avant de faire modifier des fichiers.")

        if "debug_python" in intent_names or "Python" in tech_names:
            model_advice.append("Pour le debug Python, colle la traceback complète : ça améliore fortement la qualité du diagnostic.")
            model_advice.append("Quand tu veux gagner du temps, demande directement le fichier complet corrigé plutôt qu’un mini patch.")

        if top_errors:
            recommendations.append("Des erreurs récurrentes ont été détectées : garde une étape standard de collecte d’erreur complète avant analyse.")
            model_advice.append("Sonnet reste le bon choix pour les bugs réels avec contexte. Haiku suffit pour trier ou reformuler.")
        else:
            recommendations.append("Peu d’erreurs sont visibles dans l’historique actuel : pense à coller les traceback complètes dans les sessions de debug.")

        if "VS Code" in tech_names:
            workflow_advice.append("Garde un terminal pour Claude et un autre pour l’exécution locale. Ça clarifie beaucoup le flux de travail.")

        if "Claude" in tech_names:
            recommendations.append("Ton usage est centré sur Claude Code : privilégie des conversations courtes par tâche plutôt qu’un seul long fil.")

        if "Python" in tech_names:
            recommendations.append("Ton activité Python est détectée : garde une méthode cohérente “fichier complet” pour les gros correctifs et “diff ciblé” pour les petits ajustements.")

        if "Notion" not in tech_names:
            mcp_advice.append("Notion n’apparaît pas comme usage dominant : dans ton profil DEV, coupe-le par défaut.")
        else:
            mcp_advice.append("Notion est utile mais devrait rester activé seulement dans les sessions qui l’emploient réellement.")

        mcp_advice.append("Gmail / Calendar / Drive devraient rester dans un profil PERSO distinct du profil DEV.")
        mcp_advice.append("En DEV pur, vise un profil minimal avec seulement les outils locaux nécessaires.")
        mcp_advice.append("Évite de charger des MCP “au cas où” : ça coûte du contexte pour rien.")

        model_advice.append("Réserve Sonnet aux tâches de logique, refactor et debug réel.")
        model_advice.append("Utilise Haiku pour les listes, reformulations, petites commandes et transformations simples.")
        model_advice.append("Quand tu changes de sujet, ouvre une session plus courte ou nettoie fortement le contexte.")

        profile_recommendation = {
            "recommended_profile": "DEV_MINIMAL",
            "mcp_enable": ["filesystem", "git"],
            "mcp_disable_by_default": ["claude_ai_Gmail", "claude_ai_Google_Calendar", "claude_ai_Google_Drive"],
        }

        if "Notion" in tech_names:
            profile_recommendation["mcp_enable"].append("claude_ai_Notion")

        score = 58

        if entries_cleaned >= 10:
            score += 8
        if entries_cleaned >= 20:
            score += 8
        if session_count >= 2:
            score += 5
        if session_count >= 4:
            score += 4
        if len(top_technologies) >= 3:
            score += 5
        if app_events >= 3:
            score += 6
        if app_events >= 8:
            score += 4
        if noise_ratio <= 0.6:
            score += 4
        elif noise_ratio >= 0.85:
            score -= 6
        if top_errors:
            score -= 5

        score = max(0, min(100, score))

        result = {
            "optimization_score": score,
            "history_confidence": "low" if entries_cleaned < 10 else "medium" if entries_cleaned < 25 else "high",
            "recommendations": recommendations,
            "warnings": warnings,
            "model_advice": model_advice,
            "mcp_advice": mcp_advice,
            "workflow_advice": workflow_advice,
            "detected_technologies": [name for name, _ in top_technologies],
            "detected_intents": [name for name, _ in top_intents],
            "detected_errors": [name for name, _ in top_errors],
            "history_volume": {
                "entries_cleaned": entries_cleaned,
                "entries_raw": entries_raw,
                "session_count": session_count,
                "noise_ratio": noise_ratio,
            },
            "source_breakdown": source_breakdown,
            "event_type_breakdown": event_type_breakdown,
            "profile_recommendation": profile_recommendation,
        }

        save_json(ADVISOR_REPORT_FILE, result)
        return result
