# Sample Chess Book Text
# Use this for testing the parser

SAMPLE_BOOK_TEXT = """
Chapter 1: The Italian Game - Introduction

The Italian Game is one of the oldest openings in chess, dating back to the 16th century. 
It begins with the moves 1.e4 e5 2.Nf3 Nc6 3.Bc4, where White develops rapidly and puts 
immediate pressure on Black's weak f7 square.

The opening is characterized by:
- Rapid development of pieces
- Control of the center
- Pressure on f7
- Flexible pawn structure

Main Line Analysis

After 3.Bc4, Black's main response is 3...Bc5, mirroring White's setup. This leads to 
the Giuoco Piano after 4.c3 {Preparing d4 to fight for the center} Nf6 5.d4 exd4 
6.cxd4 Bb4+ {The critical check, forcing White to make a decision}.

White has two main options:

7.Bd2 {The solid choice} Bxd2+ 8.Nbxd2 {White has a small edge due to the bishop pair 
and better central control} d6 9.0-0 0-0 10.h3 {Preventing any annoying pins}

7.Nc3 {More aggressive, accepting doubled pawns} Nxe4 8.0-0 Bxc3 9.bxc3 {The Max Lange 
Attack - White has compensation for the pawn}


Chapter 2: The Two Knights Defense

Instead of developing the bishop to c5, Black can immediately challenge the e4 pawn 
with 3...Nf6, leading to the Two Knights Defense.

This creates immediate tactical complications. White's most aggressive response is 
4.Ng5 {Attacking f7 directly} d5 {Black must fight back in the center}

Now White faces an important decision:

5.exd5 Na5 {The Pomeranian Variation, challenging the bishop} 6.Bb5+ {A good practical 
choice} c6 7.dxc6 bxc6 8.Be2 h6 9.Nf3 e4 10.Ne5 {Complex middlegame ahead}

5.exd5 Nxd5 {Accepting the pawn} 6.Nxf7! {The famous Fried Liver Attack!} Kxf7 
7.Qf3+ Ke6 {The king must advance} 8.Nc3 {White has a strong attack for the piece} 
Nce7 9.d4 c6 10.Bg5 {Very dangerous for Black}


Chapter 3: The Evans Gambit

For players who love sharp, tactical play, the Evans Gambit offers an exciting alternative.

After 3...Bc5 4.b4!? {Sacrificing a pawn for rapid development} Bxb4 5.c3 Ba5 6.d4 
{White has a strong pawn center and active pieces} exd4 7.0-0 dxc3 {7...d6 is more solid} 
8.Qb3! {Threatening both f7 and b7}

Modern theory considers this roughly equal, but White gets practical chances with:
- Strong center
- Lead in development  
- Open lines for attack
- Pressure on f7

The gambit is named after Captain William Davies Evans who first employed it in 1827.


Appendix A: Notation Guide

This book uses standard algebraic notation:
- K = King, Q = Queen, R = Rook, B = Bishop, N = Knight
- Lowercase letters (a-h) represent files
- Numbers (1-8) represent ranks
- x = capture
- + = check
- # = checkmate
- 0-0 = kingside castling
- 0-0-0 = queenside castling
- ! = good move
- !! = brilliant move
- ? = mistake
- ?? = blunder
- !? = interesting move
- ?! = dubious move
"""

# Sample PGN that should be generated from Chapter 1
EXPECTED_PGN_CHAPTER_1 = """
[Event "The Italian Game - Introduction"]
[Site "Chess Book"]

{The Italian Game is one of the oldest openings in chess, dating back to the 16th century. 
It begins with the moves 1.e4 e5 2.Nf3 Nc6 3.Bc4, where White develops rapidly and puts 
immediate pressure on Black's weak f7 square.}

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 {Preparing d4 to fight for the center} Nf6 
5. d4 exd4 6. cxd4 Bb4+ {The critical check, forcing White to make a decision} 
7. Bd2 {The solid choice} Bxd2+ 8. Nbxd2 {White has a small edge due to the bishop pair 
and better central control} d6 9. O-O O-O 10. h3 {Preventing any annoying pins}
(7. Nc3 {More aggressive, accepting doubled pawns} Nxe4 8. O-O Bxc3 9. bxc3 
{The Max Lange Attack - White has compensation for the pawn})
"""

# Test cases for the parser
TEST_CASES = {
    'simple_move_sequence': {
        'input': '1.e4 e5 2.Nf3 Nc6 3.Bc4',
        'expected_moves': ['e4', 'e5', 'Nf3', 'Nc6', 'Bc4']
    },
    'moves_with_comment': {
        'input': '1.e4 {Best by test} e5 2.Nf3 Nc6',
        'expected_pgn_has': ['{Best by test}', '1. e4', 'e5', '2. Nf3']
    },
    'variation': {
        'input': '1.e4 e5 (1...c5 {Sicilian}) 2.Nf3',
        'expected_pgn_has': ['1. e4 e5', '(1... c5 {Sicilian})']
    },
    'captures': {
        'input': '1.e4 e5 2.Nf3 Nc6 3.Bb5 a6 4.Bxc6',
        'expected_moves': ['e4', 'e5', 'Nf3', 'Nc6', 'Bb5', 'a6', 'Bxc6']
    },
    'castling': {
        'input': '1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5 4.0-0',
        'expected_moves': ['e4', 'e5', 'Nf3', 'Nc6', 'Bc4', 'Bc5', 'O-O']
    },
    'nags': {
        'input': '1.e4! e5? 2.Nf3!! Nc6?? 3.Bc4!?',
        'expected_has_nags': True
    }
}
