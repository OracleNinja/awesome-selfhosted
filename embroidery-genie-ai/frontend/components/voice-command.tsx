"use client";

/**
 * Module 11 — voice command mode.
 *
 * Speech recognition runs in the browser (Web Speech API, no audio leaves the
 * device); the transcript goes to the backend which returns a structured
 * intent. Anything the parser is not confident about is shown back to the user
 * rather than executed.
 */

import { AnimatePresence, motion } from "framer-motion";
import { Mic, MicOff } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/feedback";
import { Dialog, useToast } from "@/components/ui/overlay";
import { api, type VoiceCommand } from "@/lib/api";
import { cn, humanize } from "@/lib/utils";

type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
};

function getRecognition(): SpeechRecognitionLike | null {
  if (typeof window === "undefined") return null;
  const Ctor =
    (window as unknown as { SpeechRecognition?: new () => SpeechRecognitionLike }).SpeechRecognition ??
    (window as unknown as { webkitSpeechRecognition?: new () => SpeechRecognitionLike })
      .webkitSpeechRecognition;
  return Ctor ? new Ctor() : null;
}

export function VoiceCommandButton() {
  const router = useRouter();
  const toast = useToast();
  const [supported, setSupported] = React.useState(false);
  const [listening, setListening] = React.useState(false);
  const [open, setOpen] = React.useState(false);
  const [transcript, setTranscript] = React.useState("");
  const [result, setResult] = React.useState<VoiceCommand | null>(null);
  const [examples, setExamples] = React.useState<string[]>([]);
  const recognition = React.useRef<SpeechRecognitionLike | null>(null);

  React.useEffect(() => {
    const instance = getRecognition();
    setSupported(Boolean(instance));
    recognition.current = instance;
  }, []);

  const submit = React.useCallback(
    async (text: string) => {
      if (!text.trim()) return;
      try {
        const command = await api.voice(text);
        setResult(command);
        if (command.intent === "unknown") {
          setExamples(command.suggestions);
          setOpen(true);
          return;
        }
        // Navigation is safe to run immediately; anything that changes data
        // is surfaced for confirmation instead.
        if (command.action?.type === "navigate" && command.action.route) {
          toast.success(command.reply || "On it.");
          router.push(command.action.route);
        } else {
          setOpen(true);
        }
      } catch {
        toast.error("Could not process that command.");
      }
    },
    [router, toast],
  );

  const start = () => {
    const instance = recognition.current;
    if (!instance) {
      setOpen(true);
      return;
    }
    instance.lang = "en-US";
    instance.continuous = false;
    instance.interimResults = true;

    instance.onresult = (event) => {
      const text = Array.from(event.results)
        .map((result) => result[0].transcript)
        .join(" ");
      setTranscript(text);
    };
    instance.onerror = (event) => {
      setListening(false);
      if (event.error !== "aborted") {
        toast.error("Microphone unavailable", "Check the browser's microphone permission.");
      }
    };
    instance.onend = () => {
      setListening(false);
      setTranscript((current) => {
        void submit(current);
        return current;
      });
    };

    setTranscript("");
    setListening(true);
    instance.start();
  };

  const stop = () => {
    recognition.current?.stop();
    setListening(false);
  };

  React.useEffect(() => {
    void api
      .voiceExamples()
      .then((data) => setExamples(data.examples))
      .catch(() => undefined);
  }, []);

  return (
    <>
      <Button
        variant={listening ? "default" : "ghost"}
        size="icon-sm"
        onClick={listening ? stop : start}
        title={supported ? "Voice command" : "Voice commands (type instead)"}
        className="relative"
      >
        {listening ? (
          <>
            <span className="absolute inset-0 animate-pulse-ring rounded-lg bg-primary/40" />
            <Mic className="relative" />
          </>
        ) : supported ? (
          <Mic />
        ) : (
          <MicOff />
        )}
      </Button>

      <AnimatePresence>
        {listening ? (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="fixed left-1/2 top-16 z-40 -translate-x-1/2 rounded-full border border-primary/30 bg-card/95 px-4 py-2 text-sm shadow-xl backdrop-blur"
          >
            <span className="flex items-center gap-2">
              <span className="size-2 animate-pulse rounded-full bg-destructive" />
              {transcript || "Listening…"}
            </span>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Voice command"
        description={
          supported
            ? "Speak or type a command."
            : "This browser has no speech recognition. Type the command instead."
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Close
            </Button>
            {result?.action ? (
              <Button
                onClick={() => {
                  if (result.action?.type === "navigate" && result.action.route) {
                    router.push(result.action.route);
                  } else {
                    toast.success(result.reply || "Command queued.");
                  }
                  setOpen(false);
                }}
              >
                Run
              </Button>
            ) : null}
          </>
        }
      >
        <div className="space-y-4">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submit(transcript);
            }}
            className="flex gap-2"
          >
            <input
              value={transcript}
              onChange={(event) => setTranscript(event.target.value)}
              placeholder='e.g. "Prepare a 50 shirt order"'
              className="flex h-10 flex-1 rounded-lg border border-input bg-background/60 px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <Button type="submit">Parse</Button>
          </form>

          {result ? (
            <div className="rounded-lg border border-border/70 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={result.intent === "unknown" ? "destructive" : "success"}>
                  {humanize(result.intent)}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  confidence {(result.confidence * 100).toFixed(0)}%
                </span>
              </div>
              {result.reply ? <p className="mt-2 text-sm">{result.reply}</p> : null}
              {Object.keys(result.entities).length ? (
                <dl className="mt-3 grid grid-cols-2 gap-2 text-xs">
                  {Object.entries(result.entities).map(([key, value]) => (
                    <div key={key} className="rounded-md bg-secondary/50 px-2 py-1">
                      <dt className="text-muted-foreground">{humanize(key)}</dt>
                      <dd className="font-medium">{String(value)}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </div>
          ) : null}

          {examples.length ? (
            <div>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Try saying
              </p>
              <div className="flex flex-wrap gap-1.5">
                {examples.map((example) => (
                  <button
                    key={example}
                    onClick={() => {
                      setTranscript(example);
                      void submit(example);
                    }}
                    className={cn(
                      "rounded-full border border-border/70 px-2.5 py-1 text-xs",
                      "text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground",
                    )}
                  >
                    {example}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </Dialog>
    </>
  );
}
