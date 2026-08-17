"""Loading the local LLM into LM Studio the moment it is first needed.

LM Studio unloads a model once it has been idle for config.llm_idle_ttl_seconds,
which is deliberate: the assistant spends most of its life listening rather than
asking, and a resident twenty gigabyte model is a poor way to spend that time.
So an unloaded model is the ordinary state between questions rather than a
fault, and nothing here reports it as one. The first question after a quiet hour
pays for the load; every question after it is immediate.

Loading goes through the lms command line rather than the HTTP endpoint because
that is where the identifier, the context length and the idle timeout are set,
and util/init_local_models.sh already establishes exactly the same three.
"""

import json
import logging
import re
import subprocess

from . import config

log = logging.getLogger(__name__)


class ModelLoadError(RuntimeError):
    pass


def run_lms(arguments):
    """Run one lms subcommand and return its standard output.

    A module level function so that the tests can stand in for it: really
    loading a model costs minutes and twenty gigabytes, which no unit test
    should spend.
    """
    try:
        completed = subprocess.run([str(config.lms_command), *arguments],
                                   capture_output=True, text=True,
                                   timeout=config.llm_load_timeout_seconds)
    except (OSError, subprocess.SubprocessError) as error:
        raise ModelLoadError("cannot run %s (%s); run util/init_local_models.sh"
                             % (config.lms_command, error))
    if completed.returncode != 0:
        raise ModelLoadError("lms %s failed: %s" % (" ".join(arguments),
                                                    completed.stderr.strip() or "no diagnostic"))
    return completed.stdout


def ensure_the_configured_model_is_loaded(served_models):
    """Load the configured model unless LM Studio is already serving it.

    Returns whether a load was needed, so the caller can say so: the question
    that triggers one waits minutes for its answer and deserves an explanation.
    """
    if config.llm_model in served_models:
        return False
    load_the_configured_model()
    return True


def load_the_configured_model():
    model_key = resolve_model_key(run_lms(["ls", "--llm", "--json"]), config.llm_source_model)
    log.info("INFO loading %s as '%s'; the first question after an idle hour waits for this",
             model_key, config.llm_model)
    run_lms(["load", model_key,
             "--context-length", str(config.llm_context_length),
             "--ttl", str(config.llm_idle_ttl_seconds),
             "--identifier", config.llm_model])
    log.info("OK  loaded %s as '%s' (context %d, idle timeout %ds)",
             model_key, config.llm_model, config.llm_context_length, config.llm_idle_ttl_seconds)


def resolve_model_key(listing_json, wanted_model):
    """'lms load' wants the local model key, which is not the Hugging Face repo
    id it was downloaded from; the two differ in case and punctuation."""
    wanted = _comparable(wanted_model.split("/")[-1])
    for model in _parse_model_listing(listing_json):
        if wanted in _comparable(model.get("modelKey", "")) \
                or wanted in _comparable(model.get("path", "")):
            return model["modelKey"]
    raise ModelLoadError("no downloaded model matches %s; run util/init_local_models.sh"
                         % wanted_model)


def _comparable(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _parse_model_listing(listing_json):
    """The listing comes from another program, so it is checked, not trusted."""
    if not listing_json.strip():
        return []
    try:
        listing = json.loads(listing_json)
    except ValueError as error:
        raise ModelLoadError("cannot read the model listing from lms (%s)" % error)
    if not isinstance(listing, list):
        raise ModelLoadError("lms listed models as %s, expected a list" % type(listing).__name__)
    return [model for model in listing if isinstance(model, dict)]
