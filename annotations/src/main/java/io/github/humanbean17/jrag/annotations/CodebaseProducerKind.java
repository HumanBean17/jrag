package io.github.humanbean17.jrag.annotations;

/**
 * Async publishing mechanisms recognized by the jrag indexer. Mirrors the
 * parser's {@code VALID_PRODUCER_KINDS} vocabulary.
 */
public enum CodebaseProducerKind {
    kafka_send,
    stream_bridge_send
}
