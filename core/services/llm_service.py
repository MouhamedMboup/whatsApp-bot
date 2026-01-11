"""
Service pour l'intégration LLM (sanitisation de commentaires uniquement)

Contraintes strictes:
- Sanitisation uniquement (pas de décision, pas de jugement)
- Suppression d'insultes
- Suppression de données personnelles
- Reformulation en langage neutre et factuel
- Ne publie jamais automatiquement
"""
import logging
from typing import Tuple, Optional
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class LLMService:
    """Service pour la sanitisation de commentaires via LLM"""
    
    @staticmethod
    def sanitize_comment(comment_raw: str) -> Tuple[bool, Optional[str], str]:
        """
        Sanitise un commentaire brut via LLM
        
        Args:
            comment_raw: Commentaire brut à sanitiser
        
        Returns:
            Tuple (success, sanitized_comment, error_message)
        """
        try:
            # Vérifier que la clé API est configurée
            api_key = settings.OPENAI_API_KEY
            if not api_key:
                logger.warning("OPENAI_API_KEY non configuré, retour du commentaire original")
                return False, None, "Service de sanitisation non configuré"
            
            # Appeler l'API OpenAI pour sanitisation
            # TODO: Implémenter l'appel réel à l'API OpenAI
            # Pour l'instant, retourner une version simplifiée
            
            # Placeholder: En production, utiliser l'API OpenAI avec un prompt de sanitisation
            sanitized = LLMService._sanitize_with_openai(comment_raw, api_key)
            
            if sanitized:
                logger.info(f"Commentaire sanitisé: {len(comment_raw)} -> {len(sanitized)} caractères")
                return True, sanitized, ""
            else:
                return False, None, "Erreur lors de la sanitisation"
                
        except Exception as e:
            logger.error(f"Erreur lors de la sanitisation LLM: {str(e)}")
            return False, None, f"Erreur lors de la sanitisation: {str(e)}"
    
    @staticmethod
    def _sanitize_with_openai(comment_raw: str, api_key: str) -> Optional[str]:
        """
        Appelle l'API OpenAI pour sanitisation
        
        Args:
            comment_raw: Commentaire brut
            api_key: Clé API OpenAI
        
        Returns:
            Commentaire sanitisé ou None
        """
        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            prompt = f"""Tu es un assistant qui nettoie des commentaires d'évaluation professionnelle.

Ta tâche:
1. Supprimer toutes les insultes et langage offensant
2. Supprimer les données personnelles (noms, adresses, numéros de téléphone)
3. Reformuler en langage neutre et factuel
4. Garder le sens général du commentaire
5. Ne pas ajouter d'opinions ou de jugements

Commentaire à nettoyer:
{comment_raw}

Retourne uniquement le commentaire nettoyé, sans explications."""
            
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": "Tu es un assistant qui nettoie des commentaires professionnels."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 500
            }
            
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                sanitized = data['choices'][0]['message']['content'].strip()
                return sanitized
            else:
                logger.error(f"Erreur API OpenAI: {response.status_code} - {response.text}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur réseau lors de l'appel OpenAI: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Erreur inattendue lors de la sanitisation OpenAI: {str(e)}")
            return None
