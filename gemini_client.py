import google.generativeai as genai
import logging
import os # Import os for environment variable check if needed

# --- IMPORTANT: Ensure these are imported from your actual config.py ---
# This assumes your config.py is in the same directory or accessible via PYTHONPATH
from config import GEMINI_API_KEY, VALID_CATEGORIES, MAX_BODY_CHARS_FOR_GEMINI, LOG_LEVEL, LOG_FILE
from prompt_template import CLASSIFICATION_PROMPT # Assuming prompt_template exists

# --- CRITICAL: Logging setup for direct execution ---
# This ensures DEBUG messages are visible when you run this file directly
log_level = getattr(logging, LOG_LEVEL.upper(), logging.INFO) 
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE), # Logs to the configured file
        logging.StreamHandler()        # Also logs to console
    ]
)
# Define the logger for this specific module
logger = logging.getLogger(__name__)

# --- Global variable to hold the Gemini model instance ---
gemini_model = None

def initialize_gemini():
    """Initializes the Gemini AI model."""
    global gemini_model # Declare intent to modify the global variable
    logger.debug("Attempting to initialize Gemini AI model.")
    try:
        # --- Debugging: Verify API Key status early ---
        if not GEMINI_API_KEY:
            logger.error("GEMINI_API_KEY environment variable not set or is empty.")
            # If API key is missing, raise an error to stop execution
            raise ValueError("GEMINI_API_KEY environment variable not set.")
        else:
            logger.debug(f"GEMINI_API_KEY detected (first 5 chars): {GEMINI_API_KEY[:5]}*****")

        # Configure the generative AI library with the API key
        genai.configure(api_key=GEMINI_API_KEY)
        logger.debug("genai.configure() called successfully.")

        # Attempt to create the model instance
        # This is where the actual connection/initialization to Gemini happens
        temp_model = genai.GenerativeModel('gemini-1.5-flash-latest')
        
        # --- Debugging: Check the created model object ---
        logger.debug(f"Result of genai.GenerativeModel: {temp_model}")
        if temp_model is None:
            logger.error("genai.GenerativeModel returned None unexpectedly. This is a critical failure.")
            # If it returns None, we treat it as a failure and raise an error
            raise RuntimeError("Gemini model object could not be created (returned None).")
            
        # If successfully created, assign it to the global variable
        gemini_model = temp_model
        logger.debug(f"gemini_model (global) has been assigned. Type: {type(gemini_model)}, Value: {gemini_model}")
        # Check its boolean evaluation (important for 'if not gemini_model' checks)
        logger.debug(f"Boolean value of gemini_model: {bool(gemini_model)}") 

        logger.info("Gemini AI model initialized successfully.")

    except Exception as e:
        logger.error(f"Failed to configure Gemini AI: {e}", exc_info=True) # Log full traceback
        gemini_model = None # Ensure it's None if initialization fails
    
    logger.debug(f"Exiting initialize_gemini. Final global gemini_model state: {gemini_model}")


def classify_email_with_gemini(sender, subject, body):
    """Classifies email content using Gemini AI."""
    # This function is not run by the __main__ block, but remains for completeness
    if not gemini_model:
        logger.error("Gemini model not initialized. Defaulting to 'Promotions'.")
        return "Promotions"

    if not subject and not body:
        logger.warning("Email has no subject or body, classifying as Promotions by default.")
        return "Promotions"

    truncated_body = body[:MAX_BODY_CHARS_FOR_GEMINI]

    prompt = CLASSIFICATION_PROMPT.format(
        sender=sender,
        subject=subject,
        truncated_body=truncated_body
    )

    try:
        logger.debug(f"Sending prompt to Gemini: \n{prompt[:300]}...")
        response = gemini_model.generate_content(prompt)
        category = response.text.strip()
        if category in VALID_CATEGORIES:
            logger.info(f"Gemini classified email as: {category}")
            return category
        else:
            logger.warning(f"Gemini returned an invalid category: '{category}'. Defaulting to 'Promotions'. Response: {response.text}")
            return "Promotions"
    except Exception as e:
        logger.error(f"Error calling Gemini API: {e}. Defaulting to 'Promotions'.", exc_info=True)
        return "Promotions"

# --- Main execution block for direct testing ---
if __name__ == "__main__":
    logger.info("Running gemini_client.py directly for testing.")
    initialize_gemini()
    
    if gemini_model:
        logger.info("Gemini model is active after initialization. You should be good to go!")
        # Optional: Add a quick test generation here if you want to confirm communication
        try:
            response = gemini_model.generate_content("What is 1+1?")
            logger.info(f"Test generation from direct run: {response.text.strip()}")
        except Exception as e:
            logger.error(f"Failed during optional test generation: {e}")
    else:
        logger.critical("Gemini model is NOT active after initialization. Something went wrong.")
        # If it's not active, the logs above should show the detailed reason.
