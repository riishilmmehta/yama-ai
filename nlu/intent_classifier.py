import os
import logging
from typing import Tuple, List, Dict
import json

logger = logging.getLogger(__name__)

try:
    from setfit import SetFitModel, Trainer, TrainingArguments
    from datasets import Dataset
    SETFIT_AVAILABLE = True
except ImportError:
    SETFIT_AVAILABLE = False
    logger.warning("SetFit is not installed. Intent classification will fall back to keyword matching.")

class IntentClassifier:
    def __init__(self, model_id: str = "paraphrase-MiniLM-L3-v2", model_dir: str = "models/intent_model"):
        self.model_id = model_id
        self.model_dir = model_dir
        self.model = None
        
        if SETFIT_AVAILABLE:
            if os.path.exists(self.model_dir):
                logger.info(f"Loading intent model from {self.model_dir}")
                try:
                    self.model = SetFitModel.from_pretrained(self.model_dir)
                except Exception as e:
                    logger.error(f"Failed to load model: {e}")
            else:
                logger.info("No trained model found. You will need to train one using the provided scripts.")

    def predict(self, text: str, threshold: float = 0.6) -> Tuple[str, float]:
        """
        Predicts the intent of the given text.
        Returns a tuple of (intent_label, confidence_score).
        """
        if not self.model:
            return "unknown", 0.0
            
        try:
            # SetFit returns probabilities if we use predict_proba
            probs = self.model.predict_proba([text])[0]
            
            # Get the class with highest probability
            import numpy as np
            best_class_idx = np.argmax(probs)
            confidence = probs[best_class_idx]
            
            # We need to map index to label if the model has a label mapping
            # Assuming labels were strings during training
            if hasattr(self.model, "labels") and self.model.labels:
                intent = self.model.labels[best_class_idx]
            else:
                # Fallback: SetFitModel might just return the string label directly from predict
                intent = self.model.predict([text])[0]
                
            if confidence >= threshold:
                return str(intent), float(confidence)
            else:
                return "fallback", float(confidence)
                
        except Exception as e:
            logger.error(f"Prediction error: {e}")
            return "unknown", 0.0

    def train(self, data_path: str):
        """
        Trains the SetFit model on a provided JSON dataset.
        Expects data in format: [{"text": "...", "label": "..."}, ...]
        """
        if not SETFIT_AVAILABLE:
            raise RuntimeError("SetFit is required for training.")
            
        logger.info(f"Loading training data from {data_path}")
        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        dataset = Dataset.from_list(data)
        
        logger.info(f"Downloading base model {self.model_id}")
        self.model = SetFitModel.from_pretrained(self.model_id)
        
        args = TrainingArguments(
            batch_size=16,
            num_epochs=1,
            evaluation_strategy="no",
            save_strategy="no",
            load_best_model_at_end=False,
        )

        trainer = Trainer(
            model=self.model,
            args=args,
            train_dataset=dataset,
        )
        
        logger.info("Starting training...")
        trainer.train()
        
        logger.info(f"Saving model to {self.model_dir}")
        os.makedirs(os.path.dirname(self.model_dir), exist_ok=True)
        self.model.save_pretrained(self.model_dir)
        logger.info("Training complete.")

# Singleton instance
classifier = IntentClassifier()

def get_intent(text: str) -> Tuple[str, float]:
    return classifier.predict(text)
