import logging
from typing import List

logger = logging.getLogger(__name__)

try:
    from sumy.parsers.plaintext import PlaintextParser
    from sumy.nlp.tokenizers import Tokenizer
    from sumy.summarizers.text_rank import TextRankSummarizer
    import nltk
    SUMY_AVAILABLE = True
except ImportError:
    SUMY_AVAILABLE = False
    logger.warning("sumy is not installed. Extractive summarization will fallback to simple truncation.")

class ExtractiveSummarizer:
    def __init__(self):
        self.summarizer = None
        if SUMY_AVAILABLE:
            self.summarizer = TextRankSummarizer()
            try:
                nltk.data.find('tokenizers/punkt')
            except LookupError:
                logger.info("Downloading NLTK punkt tokenizer for sumy...")
                nltk.download('punkt')

    def summarize(self, text: str, sentences_count: int = 3) -> str:
        """
        Summarizes the text by extracting the most important sentences.
        """
        if not text or not text.strip():
            return ""
            
        if not SUMY_AVAILABLE or not self.summarizer:
            # Fallback naive summarization
            sentences = text.replace('!', '.').replace('?', '.').split('.')
            sentences = [s.strip() for s in sentences if s.strip()]
            return '. '.join(sentences[:sentences_count]) + ('.' if sentences else '')
            
        try:
            parser = PlaintextParser.from_string(text, Tokenizer("english"))
            summary_sentences = self.summarizer(parser.document, sentences_count)
            return " ".join([str(sentence) for sentence in summary_sentences])
        except Exception as e:
            logger.error(f"Error during summarization: {e}")
            return text[:500] + "..." # basic fallback

summarizer = ExtractiveSummarizer()

def extract_summary(text: str, num_sentences: int = 3) -> str:
    return summarizer.summarize(text, num_sentences)
