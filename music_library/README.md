# Music library (royalty-free tracks)

If `music_library/<mood>/` has audio files, `music_engine.py` uses one of them for half the
videos (`STUDIO_MUSIC_LIBRARY_SHARE`, default 0.5) and an original FluidSynth score for the other
half. The track is picked at random, looped or trimmed to length, faded, and loudness-normalised.

`python setup_music_tools.py` downloads a starter set of 16 Kevin MacLeod tracks
(CC BY 4.0, with credit files). Skip that step with `--no-tracks`. The audio is git-ignored,
so only this README is committed.

Mood folders: `emotional`, `happy`, `festive`, `adventure`, `tech`, `inspiring`, `mystery`
(prompts about IT/AI → `tech`, or `inspiring` for career/training/transformation stories,
or `mystery` for outages/incidents/debugging stories).

Formats: `.mp3 .wav .ogg .m4a .flac .aac`

## Free sources

| Source | Licence | Credit needed? |
|---|---|---|
| [Pixabay Music](https://pixabay.com/music/) | Pixabay Content License, commercial use OK | No |
| [YouTube Audio Library](https://studio.youtube.com/) (Studio → Audio Library) | Per track | Only on tracks marked "attribution required" |
| [Incompetech / Kevin MacLeod](https://incompetech.com/music/royalty-free/) | CC BY 4.0 | Yes |
| [Free Music Archive](https://freemusicarchive.org/) | Varies per track | Check each one, and skip any marked NC (non-commercial) |

## Credits

If a track needs attribution, put the credit line in a text file next to it, using the
same name plus `.credit.txt`:

```
tech/cyber-pulse.mp3
tech/cyber-pulse.credit.txt   ← "Cyber Pulse" by X (CC BY 4.0) https://...
```

The credit line then appears in the Studio answer for that video.

## Forcing a source

Set `STUDIO_MUSIC_SOURCE` to `library`, `midi` or `synth` (default `auto` = library → midi → synth).
