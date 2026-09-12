import { useCallback, useEffect, useRef, useState } from 'react';
import { DECLARED_MODELS, type ModelProfile, type Services } from './index';

export type ModelRegistry = { models: ModelProfile[]; loading: boolean; error: string; refresh: () => void };
const unavailable = (models: readonly ModelProfile[], reason: string): ModelProfile[] =>
  models.map(model => ({ ...model, capabilities: [...model.capabilities], available: false, reason }));

function validatedModels(value: unknown): ModelProfile[] {
  if (!Array.isArray(value)) throw new Error('The model service returned an invalid registry.');
  const ids = new Set<string>();
  const capabilities = new Set(['language', 'classification', 'localization']);
  for (const item of value) {
    if (!item || typeof item !== 'object' || typeof item.id !== 'string' || !item.id.trim() || ids.has(item.id) ||
      typeof item.label !== 'string' || !item.label.trim() || typeof item.available !== 'boolean' ||
      (item.reason !== undefined && typeof item.reason !== 'string') ||
      (item.revision !== undefined && typeof item.revision !== 'string') ||
      !Array.isArray(item.capabilities) || item.capabilities.some((capability: unknown) => typeof capability !== 'string' || !capabilities.has(capability))) {
      throw new Error('The model service returned an invalid registry.');
    }
    ids.add(item.id);
  }
  const received = value as ModelProfile[];
  return [...DECLARED_MODELS.map(model => received.find(item => item.id === model.id) ?? { ...model, available: false, reason: 'Not provided by this service' }),
    ...received.filter(model => !DECLARED_MODELS.some(declared => declared.id === model.id))];
}

/** Shared by assistant and comparison surfaces. Refresh failures revoke stale availability. */
export function useModelRegistry(services: Services): ModelRegistry {
  const [state, setState] = useState({ services, models: unavailable(DECLARED_MODELS, 'Status not checked'), loading: true, error: '' });
  const generation = useRef(0);
  const refresh = useCallback(() => {
    const request = ++generation.current;
    if (!services.connected) {
      setState({ services, models: unavailable(DECLARED_MODELS, 'Model service is not configured'), loading: false, error: '' });
      return;
    }
    setState(previous => ({ services, models: unavailable(previous.services === services ? previous.models : DECLARED_MODELS, 'Checking model status…'), loading: true, error: '' }));
    void services.listModels().then(items => {
      const models = validatedModels(items);
      if (request === generation.current) setState({ services, models, loading: false, error: '' });
    }).catch(reason => {
      if (request !== generation.current) return;
      const detail = reason instanceof Error ? reason.message : 'The model service could not be reached.';
      setState(previous => ({ services, models: unavailable(previous.models, 'Availability could not be verified'), loading: false, error: `Model status could not be refreshed. ${detail}` }));
    });
  }, [services]);
  useEffect(() => { refresh(); return () => { generation.current += 1; }; }, [refresh]);
  if (state.services !== services) return { models: unavailable(DECLARED_MODELS, 'Checking model status…'), loading: true, error: '', refresh };
  return { models: state.models, loading: state.loading, error: state.error, refresh };
}
