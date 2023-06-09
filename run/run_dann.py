import argparse
import torch
import torch.nn as nn
import numpy as np
import torch.optim as optim
from tqdm import tqdm, trange
import sys
sys.path.append("..")
from models.ADDA import MLP, Classifier
from utils import load_data, seed_all, weight_init, GRL
from sklearn.metrics import accuracy_score
import os


def evaluate(model, data_loader, device):
    model.eval()
    acc = []
    with torch.no_grad():
        for X, y in data_loader:
            X, y = X.to(device), y.to(device)
            pred = model(X)
            pred = pred.detach().cpu().numpy().astype('float64')
            pred = np.argmax(pred, axis=-1)
            y = y.detach().cpu().numpy()

            accuracy = accuracy_score(y, pred)
            acc.append(accuracy)
    acc = np.mean(acc)
    return acc


def main(args, i, save_path):
    seed_all(42)
    device = torch.device(f"cuda:{args.gpu}" if (torch.cuda.is_available() and args.gpu >= 0) else "cpu")

    # load data
    source_loader, target_loader = load_data(i, args)
    print("Data loaded.")

    # create model
    encoder = MLP(args.encoder_units, args.dropout)
    predictor = Classifier(args.predictor_units, args.dropout, num_class=4)
    discriminator = Classifier(args.discriminator_units, args.dropout, num_class=2)


    encoder.apply(weight_init)
    predictor.apply(weight_init)
    discriminator.apply(weight_init)

    encoder.to(device)
    predictor.to(device)
    discriminator.to(device)
    print("Model created.")

    # create loss function and optimizer
    criterion_pred = nn.CrossEntropyLoss()
    criterion_discrim = nn.CrossEntropyLoss()

    optimizer_encoder = optim.Adam(encoder.parameters(), lr=args.lr, weight_decay=args.wd)
    optimizer_predictor = optim.Adam(predictor.parameters(), lr=args.lr, weight_decay=args.wd)
    optimizer_discrim = optim.Adam(discriminator.parameters(), lr=1e-3, weight_decay=1e-2)

    print("Start training.")

    sp_best = 0.
    kill_cnt = 0
    for epoch in range(args.epochs):
        loss_all = []
        encoder.train()
        discriminator.train()
        predictor.train()

        for (tgt_data, _), (src_data, src_label) in zip(target_loader, source_loader):
            tgt_data, src_data, src_label = tgt_data.to(device), src_data.to(device), src_label.to(device)

            optimizer_discrim.zero_grad()
            optimizer_encoder.zero_grad()
            optimizer_predictor.zero_grad()

            src_feat = encoder(src_data)
            tgt_feat = encoder(tgt_data)

            src_pred = discriminator(GRL.apply(src_feat, 2/(1+np.exp(-100*epoch/args.epochs))-1))
            tgt_pred = discriminator(GRL.apply(tgt_feat, 2/(1+np.exp(-100*epoch/args.epochs))-1))
            src_label_pred = predictor(src_feat)

            src_labels = torch.ones(src_pred.shape).to(device)
            tgt_labels = torch.zeros(tgt_pred.shape).to(device)

            loss_discrim = criterion_discrim(torch.cat((src_pred, tgt_pred)), torch.cat((src_labels, tgt_labels)))
            loss_pred = criterion_pred(src_label_pred, src_label)
            loss_total = loss_discrim + loss_pred
            loss_total.backward()

            optimizer_encoder.step()
            optimizer_discrim.step()
            optimizer_predictor.step()

            loss_all.append(loss_total.item())
        loss_all = np.mean(loss_all)

        # evulate
        acc = evaluate(nn.Sequential(encoder, predictor), target_loader, device)

        print(f"Epoch {epoch}, train loss: {loss_all:.4f}, val acc: {acc:.4f}")
        if acc > sp_best:
            sp_best = acc
            torch.save(encoder.state_dict(), save_path+"encoder")
            torch.save(predictor.state_dict(), save_path+"predictor")
            print("saving new best model")
        # else:
        #     kill_cnt += 1
        #     if kill_cnt >= 3:
        #         print(f"early stop, best acc: {sp_best:.4f}")
        #     break


    torch.save(discriminator, save_path+"discriminator")

    # Third, test step. Predict target
    print("Start testing.")
    predictor.load_state_dict(torch.load(save_path+"predictor"))
    encoder.load_state_dict(torch.load(save_path+"encoder"))
    target_predictor = nn.Sequential(encoder, predictor)
    acc = evaluate(target_predictor, target_loader, device)
    print(f"Test on person {i}, acc: {acc:.4f}")
    return acc


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch_size", type=int, default=512)
    parser.add_argument("--epochs", type=int, default=500)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--wd", type=float, default=1e-2)
    parser.add_argument("--dropout", type=float, default=0.2)
    parser.add_argument("--encoder_units", type=int, nargs='+', default=[310, 256, 128])
    parser.add_argument("--predictor_units", type=int, nargs='*', default=[128, 64, 32])
    parser.add_argument("--discriminator_units", type=int, nargs='*', default=[128, 48])
    parser.add_argument("--gamma", type=float, default=1)
    parser.add_argument("--gpu", type=int, default=0)
    args = parser.parse_args()

    os.makedirs("../ckpts", exist_ok=True)
    os.makedirs("../ckpts/dann", exist_ok=True)
    acc = []
    for i in range(1, 16):
        os.makedirs(f"../ckpts/dann/{i}", exist_ok=True)
        cur_acc = main(args, i, save_path=f"../ckpts/dann/{i}/")
        acc.append(cur_acc)
    print(f"average acc: {np.mean(acc)}")
