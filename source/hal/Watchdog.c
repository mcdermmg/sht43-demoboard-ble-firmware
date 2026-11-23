////////////////////////////////////////////////////////////////////////////////
//  S E N S I R I O N   AG,  Laubisruetistr. 50, CH-8712 Staefa, Switzerland
////////////////////////////////////////////////////////////////////////////////
// Copyright (c) 2023, Sensirion AG
// All rights reserved.
//
// Redistribution and use in source and binary forms, with or without
// modification, are permitted provided that the following conditions are met:
//
// 1. Redistributions of source code must retain the above copyright notice,
// this list of conditions and the following disclaimer.
//
// 2. Redistributions in binary form must reproduce the above copyright notice,
// this list of conditions and the following disclaimer in the documentation
// and/or other materials provided with the distribution.
//
// 3. Neither the name of the copyright holder nor the names of its
// contributors may be used to endorse or promote products derived from this
// software without specific prior written permission.
//
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS “AS IS”
// AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
// IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
// ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
// LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
// CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
// SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
// INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
// CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
// ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
// POSSIBILITY OF SUCH DAMAGE.
////////////////////////////////////////////////////////////////////////////////

/// @file Watchdog.c
#include "Watchdog.h"

#include "stm32wbxx_hal.h"

/// Key to start the watchdog
#define IWDG_KEY_START 0xCCCCU
/// Key to unlock the prescaler and reload registers
#define IWDG_KEY_WRITE_ACCESS 0x5555U
/// Key to reload the counter
#define IWDG_KEY_RELOAD 0xAAAAU
/// Prescaler register value for LSI / 256 (~125 Hz with a 32 kHz LSI)
#define IWDG_PRESCALER_DIV256 0x6U
/// Maximum reload value; 4095 / 125 Hz = ~32 s timeout
#define IWDG_RELOAD_MAX 0xFFFU

void Watchdog_Start() {
#if defined(DEBUG)
  // do not reset the device while it is halted by the debugger
  LL_DBGMCU_APB1_GRP1_FreezePeriph(LL_DBGMCU_APB1_GRP1_IWDG_STOP);
#endif
  // starting the watchdog also starts the LSI clock it runs from
  IWDG->KR = IWDG_KEY_START;
  IWDG->KR = IWDG_KEY_WRITE_ACCESS;
  IWDG->PR = IWDG_PRESCALER_DIV256;
  IWDG->RLR = IWDG_RELOAD_MAX;
  // wait until the new prescaler and reload values are taken over
  while ((IWDG->SR & (IWDG_SR_PVU | IWDG_SR_RVU)) != 0U) {
  }
  IWDG->KR = IWDG_KEY_RELOAD;
}

void Watchdog_Kick() {
  IWDG->KR = IWDG_KEY_RELOAD;
}
