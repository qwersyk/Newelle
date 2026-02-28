import math
import struct
import array

try:
    from pysilero_vad import SileroVoiceActivityDetector
    DEPENDENCIES_AVAILABLE = True
except ImportError:
    DEPENDENCIES_AVAILABLE = False


class VoiceActivityDetector:
    """Unified Voice Activity Detector using Silero VAD with energy-based fallback
    
    Uses pysilero_vad for accurate voice activity detection when available,
    falls back to energy-based detection otherwise.
    """
    
    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._use_silero = False
        self._silero_vad = None
        self._chunk_samples = 512
        
        self._energy_threshold = 0.015
        self._speech_frames_threshold = 2
        self._silence_frames_threshold = 20
        self._speech_frame_count = 0
        self._silence_frame_count = 0
        self._is_speaking = False
        
        self._noise_floor = 0.01
        self._noise_samples = []
        self._max_noise_samples = 50
        
        self._try_load_silero()
    
    def _try_load_silero(self):
        """Try to load Silero VAD model"""
        if DEPENDENCIES_AVAILABLE:
            try:
                self._silero_vad = SileroVoiceActivityDetector()
                self._use_silero = True
                self._chunk_samples = self._silero_vad.chunk_samples()
                print("VAD: Using Silero VAD")
            except Exception as e:
                print(f"VAD: Silero VAD failed to initialize: {e}, falling back to energy-based")
                self._use_silero = False
        else:
            print("VAD: pysilero-vad not available, using energy-based detection")
    
    @property
    def is_silero_available(self) -> bool:
        return self._use_silero
    
    @property
    def is_speaking(self) -> bool:
        """Returns True if currently in speech state (Call-compatible)"""
        return self._is_speaking
    
    def chunk_samples(self) -> int:
        """Returns the number of samples expected per chunk"""
        return self._chunk_samples
    
    def _calculate_energy(self, audio_data: bytes) -> float:
        """Calculate RMS energy of audio chunk"""
        if len(audio_data) == 0:
            return 0
        count = len(audio_data) // 2
        if count == 0:
            return 0
        format_str = "<" + str(count) + "h"
        try:
            shorts = struct.unpack(format_str, audio_data)
            normalized = [s / 32768.0 for s in shorts]
            rms = math.sqrt(sum(s * s for s in normalized) / len(normalized))
            return rms
        except Exception:
            return 0
    
    def _update_noise_floor(self, energy: float):
        """Update adaptive noise floor"""
        if not self._is_speaking and energy < self._energy_threshold * 2:
            self._noise_samples.append(energy)
            if len(self._noise_samples) > self._max_noise_samples:
                self._noise_samples.pop(0)
            if len(self._noise_samples) >= 10:
                self._noise_floor = sum(self._noise_samples) / len(self._noise_samples)
                self._energy_threshold = max(0.01, self._noise_floor * 2.5)
    
    def is_speech(self, frame: bytes) -> bool:
        """Check if a single audio frame contains speech
        
        Args:
            frame: Raw audio bytes (16-bit PCM)
            
        Returns:
            True if speech is detected, False otherwise
        """
        return self.get_speech_probability(frame) >= 0.5
    
    def get_speech_probability(self, frame: bytes) -> float:
        """Get speech probability for a single audio frame
        
        Args:
            frame: Raw audio bytes (16-bit PCM)
            
        Returns:
            Probability value (0.0 to 1.0), 0.5+ means speech detected
        """
        if self._use_silero and self._silero_vad is not None:
            try:
                return self._silero_vad(frame)
            except Exception:
                pass
        
        energy = self._calculate_energy(frame)
        self._update_noise_floor(energy)
        if energy > self._energy_threshold:
            return 0.8
        return 0.0
    
    def process_chunk(self, audio_data: bytes) -> tuple[bool, bool, bool]:
        """Process audio chunk and return VAD state (Call-compatible interface)
        
        Returns:
            tuple: (is_speech, speech_started, speech_ended)
        """
        if self._use_silero and self._silero_vad is not None:
            try:
                probability = self._silero_vad(audio_data)
                is_speech = probability >= 0.5
            except Exception:
                is_speech = False
        else:
            energy = self._calculate_energy(audio_data)
            self._update_noise_floor(energy)
            is_speech = energy > self._energy_threshold
        
        speech_started = False
        speech_ended = False
        
        if is_speech:
            self._speech_frame_count += 1
            self._silence_frame_count = 0
            
            if not self._is_speaking and self._speech_frame_count >= self._speech_frames_threshold:
                self._is_speaking = True
                speech_started = True
        else:
            self._silence_frame_count += 1
            if self._silence_frame_count > 5:
                self._speech_frame_count = 0
            
            if self._is_speaking and self._silence_frame_count >= self._silence_frames_threshold:
                self._is_speaking = False
                speech_ended = True
        
        return is_speech, speech_started, speech_ended
    
    def get_energy(self, audio_data: bytes) -> float:
        """Get current energy level of audio data"""
        return self._calculate_energy(audio_data)
    
    def reset(self):
        """Reset VAD state"""
        self._speech_frame_count = 0
        self._silence_frame_count = 0
        self._is_speaking = False
        self._noise_samples = []
        self._noise_floor = 0.01
        self._energy_threshold = 0.015
